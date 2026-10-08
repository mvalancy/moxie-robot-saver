"""
SDK / robot-cloud unit tests — pure Python, no broker or browser (runs fast in CI's
`pytest sim/tests`). Covers the RemoteChat response contract built in M1:
ResultCode fidelity, scored output, and action passthrough.
See docs/architecture/ai-seam.md §2 + ROADMAP.md.
"""
import pytest

from moxie_sdk.types import Reply, Action, ActionType, ResultCode
from moxie_sdk.wire import build_chat_response, build_activity_response


def test_resultcode_values_match_recovered_proto():
    # verbatim from embodied/robotbrain/RemoteChat.proto
    assert ResultCode.SUCCESS == 0
    assert ResultCode.ERROR_OFFLINE == 4
    assert ResultCode.REPLY_PENDING == 9


def test_default_response_is_success_as_the_integer():
    resp = build_chat_response("evt-1", "Hi there!")
    assert resp["command"] == "remote_chat"
    assert resp["result"] == ResultCode.SUCCESS == 0     # the uint32 VALUE, never the name
    assert resp["output"]["text"] == "Hi there!"
    assert resp["output"]["markup"] == "Hi there!"   # defaults to text
    assert resp["event_id"] == "evt-1"


def test_offline_reply_signals_error_offline():
    r = Reply.offline()
    assert r.result_code is ResultCode.ERROR_OFFLINE
    resp = build_chat_response("evt-2", r.text, result=r.result_code)
    assert resp["result"] == ResultCode.ERROR_OFFLINE    # robot uses its local fallback

def test_scored_output_fields_optional():
    bare = build_chat_response("e", "hi")
    assert "mood" not in bare["output"] and "dialog_act" not in bare["output"]
    scored = build_chat_response("e", "yay!", mood="positive", dialog_act="comment")
    assert scored["output"]["mood"] == "positive"
    assert scored["output"]["dialog_act"] == "comment"


def test_action_passthrough():
    actions = [Action(type=ActionType.LAUNCH, module_id="OPENMOXIE_CHAT",
                      content_id="memory")]
    resp = build_chat_response("e", "let's play", actions=actions)
    ra = resp["response_actions"]
    assert len(ra) == 1
    assert ra[0]["action"] == "launch"
    assert ra[0]["module_id"] == "OPENMOXIE_CHAT"
    assert ra[0]["content_id"] == "memory"


def test_the_older_exit_spelling_is_still_read_and_goes_out_as_exit_module():
    """`{"type": "exit"}` is what the webhook contract documents (webhook_app.py:11-12,
    moxie-as-a-platform.md) and what this SDK spelled until 2026-10. `ActionType._missing_`
    keeps reading it; the wire carries the ActionID name (RemoteChat.proto:260)."""
    assert ActionType("exit") is ActionType.EXIT is ActionType("exit_module")
    assert ActionType.EXIT.value == "exit_module"
    resp = build_chat_response("e", "Bye!", actions=[Action(type=ActionType("exit"))])
    assert [a["action"] for a in resp["response_actions"]] == ["exit_module"]


def test_a_raw_proto_int_result_is_accepted_and_serialises_as_the_integer():
    # a caller passing the raw proto int is read as that ResultCode and goes out as the int
    resp = build_chat_response("e", "hi", result=4)
    assert resp["result"] == ResultCode.ERROR_OFFLINE == 4 and type(resp["result"]) is int


# ---- build_activity_response (the `query_result` / CloudQueryResponse encoder) ----

@pytest.mark.parametrize("query, field, empty", [
    ("schedule", "schedule", {}),                    # field 6, NOT a generic `result`
    ("mentor_behaviors", "mentor_behaviors", []),    # field 10, repeated MentorBehavior
    ("license", "license_values", []),               # field 5, not `license`
])
def test_activity_response_keys_each_query_by_its_proto_field(query, field, empty):
    """CloudQueryResponse: request_id (field 3) echoed, the payload under its own field."""
    resp = build_activity_response(query, request_id="req-42")
    assert resp["command"] == "query_result" and resp["query"] == query
    assert resp["request_id"] == "req-42"
    assert resp[field] == empty
    assert "result" not in resp and (field == query or query not in resp)


def test_activity_response_carries_a_real_payload():
    plan = {"provided_schedule": [{"module_id": "DM", "content_id": "default"}]}
    resp = build_activity_response("schedule", plan, "req-1")
    assert resp["schedule"] is plan


def test_activity_response_omits_request_id_when_absent():
    # nothing to correlate → no null request_id on the wire
    assert "request_id" not in build_activity_response("schedule")


def test_activity_response_empty_defaults_are_not_shared():
    a = build_activity_response("mentor_behaviors")
    a["mentor_behaviors"].append({"module_id": "X"})
    assert build_activity_response("mentor_behaviors")["mentor_behaviors"] == []


def test_activity_response_response_code_optional():
    assert "response_code" not in build_activity_response("schedule", request_id="r")
    coded = build_activity_response("schedule", request_id="r",
                                    response_code="QUERY_NO_CHANGE")
    assert coded["response_code"] == "QUERY_NO_CHANGE"


def test_activity_response_rejects_unknown_query():
    with pytest.raises(ValueError):
        build_activity_response("not_a_cloud_query")
