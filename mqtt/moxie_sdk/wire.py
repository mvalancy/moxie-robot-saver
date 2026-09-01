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
                        mood=None, dialog_act=None, modules=None) -> dict:
    """Build the RemoteChatResponse JSON.

    Matches embodied/robotbrain/RemoteChat.proto: `result` is the ResultCode enum
    NAME, `output` is a RemoteChatOutput (text/markup + optional scored fields),
    `response_actions` are RemoteChatActions."""
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
    return resp
