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
from moxie_sdk.wire import build_chat_response  # noqa: E402 (pure — no broker dep)


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
