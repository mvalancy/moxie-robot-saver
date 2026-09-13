"""Hermetic controls for counts-only live action-tag campaign classification."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "mqtt"))
sys.path.insert(0, str(REPO / "sim" / "tools"))

import moxie_sdk.chat as chat  # noqa: E402
from moxie_sdk.apps import LLMApp  # noqa: E402
from moxie_sdk.types import ActionType, ChildProfile, RobotContext, Turn  # noqa: E402
from action_tag_campaign import CampaignRecord, RecordingClient, sanitized_state  # noqa: E402
from run_live_action_tags import run_supervised  # noqa: E402


def _response(content):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content))])


class _ServerError(Exception):
    status_code = 503


class _SequenceClient:
    def __init__(self, events):
        self.events = iter(events)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **_kwargs):
        event = next(self.events)
        if isinstance(event, BaseException):
            raise event
        return event


def _exercise(events, *, authoritative_delta=0):
    record = CampaignRecord("goodbye", 3, 6)
    client = RecordingClient(_SequenceClient(events), record)
    app = LLMApp("http://unused.invalid/v1", "unused", client=client)
    robot = RobotContext(device_id="synthetic", child=ChildProfile(nickname="Sam"))
    chat.reset_model_calls()
    original = chat.call_with_backoff
    original_limit = os.environ.get("MOXIE_MODEL_CALL_LIMIT")
    os.environ["MOXIE_MODEL_CALL_LIMIT"] = "6"
    chat.call_with_backoff = lambda fn, **kw: original(fn, sleep=lambda _seconds: None, **kw)
    try:
        for speech in ("bye one", "bye two", "bye three"):
            before = record.completed
            reply = app.respond(Turn(robot=robot, speech=speech))
            if record.completed == before:
                break
            record.trial(
                exit_hit=any(a.type is ActionType.EXIT for a in reply.actions),
                output_ok=bool(reply.text.strip()) and "<" not in reply.text,
            )
    finally:
        chat.call_with_backoff = original
        if original_limit is None:
            os.environ.pop("MOXIE_MODEL_CALL_LIMIT", None)
        else:
            os.environ["MOXIE_MODEL_CALL_LIMIT"] = original_limit
    return record.finish(chat.model_calls() + authoritative_delta)


class CampaignClassificationTests(unittest.TestCase):
    def tearDown(self):
        chat.reset_model_calls()

    def test_completed_two_of_three_passes(self):
        result = _exercise([_response("<exit>bye"), _response("<exit>later"),
                            _response("see you")])
        self.assertEqual((result["attempts"], result["completed"], result["exit_hits"]),
                         (3, 3, 2))
        self.assertEqual((result["measurement"], result["adherence"]),
                         ("completed", "pass"))

    def test_completed_one_of_three_fails(self):
        result = _exercise([_response("<exit>bye"), _response("later"),
                            _response("see you")])
        self.assertEqual((result["measurement"], result["adherence"]),
                         ("completed", "fail"))

    def test_two_successes_then_exhaustion_is_inconclusive(self):
        result = _exercise([_response("<exit>bye"), _response("<exit>later"),
                            _ServerError("private-a"), _ServerError("private-b"),
                            _ServerError("private-c"), _ServerError("private-d")])
        self.assertEqual((result["attempts"], result["completed"], result["exit_hits"]),
                         (6, 2, 2))
        self.assertEqual((result["measurement"], result["adherence"], result["termination"]),
                         ("inconclusive", "not_evaluated", "budget_exhausted"))

    def test_retry_recovery_counts_every_attempt(self):
        result = _exercise([_ServerError("private"), _response("<exit>bye"),
                            _response("<exit>later"), _response("done")])
        self.assertEqual((result["attempts"], result["responses_received"], result["completed"]),
                         (4, 3, 3))
        self.assertEqual(result["adherence"], "pass")

    def test_empty_or_malformed_response_is_not_a_completion(self):
        for response in (_response(""), SimpleNamespace(choices=[])):
            with self.subTest(response=response):
                result = _exercise([response])
                self.assertEqual(result["completed"], 0)
                self.assertEqual(result["measurement"], "inconclusive")
                self.assertEqual(result["last_upstream_category"], "invalid_response")

    def test_missing_prerequisite_is_visible_and_costs_zero(self):
        record = CampaignRecord("goodbye", 3, 6)
        result = record.skipped()
        self.assertEqual((result["measurement"], result["termination"], result["attempts"]),
                         ("skipped", "missing_prerequisite", 0))

    def test_counter_disagreement_is_instrument_error(self):
        result = _exercise([_response("<exit>bye"), _response("<exit>later"),
                            _response("done")], authoritative_delta=1)
        self.assertEqual((result["measurement"], result["termination"]),
                         ("error", "instrument_error"))

    def test_supervisor_reports_known_attempt_on_hanging_untrusted_client(self):
        secret = "SECRET_DO_NOT_FORWARD"
        code = """
import time
from action_tag_campaign import CampaignRecord, RecordingClient
class C:
    class Chat:
        class Completions:
            def create(self, **kwargs):
                print(%r)
                time.sleep(30)
        completions = Completions()
    chat = Chat()
r = CampaignRecord('goodbye', 3, 6, %r)
RecordingClient(C(), r).chat.completions.create()
""" % (secret, "STATE_PATH")
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            code = code.replace("STATE_PATH", str(state))
            env = os.environ.copy()
            env["PYTHONPATH"] = str(REPO / "sim" / "tools")
            result, exit_code = run_supervised(
                [sys.executable, "-c", code], timeout_seconds=0.1,
                state_path=state, env=env)
        rendered = json.dumps(result, sort_keys=True)
        self.assertEqual(exit_code, 1)
        self.assertEqual((result["termination"], result["attempts"]), ("timeout", 1))
        self.assertNotIn(secret, rendered)

    def test_secret_like_model_and_exception_text_never_enters_summary(self):
        secret = "SYNTHETIC_SECRET_LIKE_MODEL_INSTRUCTION"
        completed = _exercise([_response(f"<exit>{secret}"), _response("<exit>ok"),
                               _response("done")])
        failed = _exercise([RuntimeError(secret)])
        self.assertNotIn(secret, json.dumps(completed, sort_keys=True))
        self.assertNotIn(secret, json.dumps(failed, sort_keys=True))

    def test_unallowlisted_state_is_rejected_instead_of_echoed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({"campaign": "SECRET_DO_NOT_ECHO", "schema": 1}))
            self.assertIsNone(sanitized_state(path))


if __name__ == "__main__":
    unittest.main()
