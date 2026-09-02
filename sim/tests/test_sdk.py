"""
SDK / robot-cloud unit tests — pure Python, no broker or browser (runs fast in CI's
`pytest sim/tests`). Covers the RemoteChat response contract built in M1:
ResultCode fidelity, scored output, and action passthrough.
See docs/architecture/ai-seam.md §2 + docs/architecture/implementation-plan.md.
"""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.types import Reply, Action, ActionType, ResultCode  # noqa: E402
from moxie_sdk.wire import build_chat_response, build_activity_response  # noqa: E402 (pure)


def test_resultcode_values_match_recovered_proto():
    # verbatim from embodied/robotbrain/RemoteChat.proto
    assert ResultCode.SUCCESS == 0
    assert ResultCode.ERROR_OFFLINE == 4
    assert ResultCode.REPLY_PENDING == 9


def test_default_response_is_success_by_name():
    resp = build_chat_response("evt-1", "Hi there!")
    assert resp["command"] == "remote_chat"
    assert resp["result"] == "SUCCESS"          # wire value is the enum NAME, not "OK"
    assert resp["output"]["text"] == "Hi there!"
    assert resp["output"]["markup"] == "Hi there!"   # defaults to text
    assert resp["event_id"] == "evt-1"


def test_offline_reply_signals_error_offline():
    r = Reply.offline()
    assert r.result_code is ResultCode.ERROR_OFFLINE
    resp = build_chat_response("evt-2", r.text, result=r.result_code)
    assert resp["result"] == "ERROR_OFFLINE"    # robot uses its local fallback

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


def test_int_result_is_coerced_to_name():
    # a caller passing the raw proto int still serializes to the enum name
    resp = build_chat_response("e", "hi", result=4)
    assert resp["result"] == "ERROR_OFFLINE"


# ---- build_activity_response (the `query_result` / CloudQueryResponse encoder) ----

def test_activity_response_echoes_request_id_and_keys_schedule():
    # CloudQueryResponse: request_id (field 3) echoed from the request, the day's plan
    # under `schedule` (field 6) — NOT a generic `result` key.
    resp = build_activity_response("schedule", request_id="req-42")
    assert resp["command"] == "query_result"
    assert resp["query"] == "schedule"
    assert resp["request_id"] == "req-42"
    assert resp["schedule"] == {}
    assert "result" not in resp


def test_activity_response_keys_mentor_behaviors_as_a_list():
    resp = build_activity_response("mentor_behaviors", request_id="req-7")
    assert resp["request_id"] == "req-7"
    assert resp["mentor_behaviors"] == []      # field 10, repeated MentorBehavior
    assert "result" not in resp


def test_activity_response_license_uses_license_values():
    # field 5 is `license_values` (repeated LicenseRecord), not `license`
    resp = build_activity_response("license", request_id="r")
    assert resp["license_values"] == []
    assert "license" not in resp


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
    import pytest
    with pytest.raises(ValueError):
        build_activity_response("not_a_cloud_query")
