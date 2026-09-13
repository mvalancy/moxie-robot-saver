"""
Rate-limit / backoff / pacing tests (the AI-seam resilience) — pure, no network.
A busy gateway should slow us down and recover, not fail the child.
"""
import os
import sys
from pathlib import Path

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.chat import (  # noqa: E402
    is_rate_limit_error, is_offline_error, is_server_error,
    call_with_backoff, model_calls, note_model_call, reset_model_calls,
    ModelCallBudgetExceeded, Pacer,
)
from moxie_sdk.content import ContentApp, load_module  # noqa: E402
from moxie_sdk.types import Turn, RobotContext, ChildProfile, ResultCode  # noqa: E402


class _RateLimit(Exception):
    status_code = 429


class _ServerErr(Exception):
    status_code = 503


def test_classification():
    assert is_rate_limit_error(_RateLimit()) is True
    assert is_server_error(_ServerErr()) is True
    assert is_offline_error(ConnectionError()) is True
    assert is_rate_limit_error(ValueError()) is False


def test_backoff_retries_then_succeeds():
    calls = {"n": 0}
    waits = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _RateLimit()
        return "ok"

    out = call_with_backoff(flaky, base=0.01, on_backoff=lambda a, d, e: waits.append(d),
                            sleep=lambda s: None)
    assert out == "ok"
    assert calls["n"] == 3
    assert len(waits) == 2 and waits[1] > waits[0]      # exponential growth


def test_backoff_gives_up_after_max_retries():
    def always():
        raise _RateLimit()
    with pytest.raises(_RateLimit):
        call_with_backoff(always, max_retries=2, base=0.01, sleep=lambda s: None)


def test_model_call_campaign_allows_six_attempts_then_refuses_before_seven(monkeypatch):
    monkeypatch.setenv("MOXIE_MODEL_CALL_LIMIT", "6")
    reset_model_calls()
    for _ in range(6):
        note_model_call()
    assert model_calls() == 6
    with pytest.raises(ModelCallBudgetExceeded, match="6/6"):
        note_model_call()
    assert model_calls() == 6
    reset_model_calls()


def test_model_call_campaign_stops_retry_amplification_before_request(monkeypatch):
    monkeypatch.setenv("MOXIE_MODEL_CALL_LIMIT", "2")
    reset_model_calls()
    outbound = {"n": 0}

    def transient():
        note_model_call()
        outbound["n"] += 1
        raise _ServerErr()

    with pytest.raises(ModelCallBudgetExceeded, match="2/2"):
        call_with_backoff(transient, max_retries=4, base=0.01, sleep=lambda _: None)
    assert outbound["n"] == model_calls() == 2
    reset_model_calls()


def test_invalid_model_call_campaign_limit_fails_closed(monkeypatch):
    monkeypatch.setenv("MOXIE_MODEL_CALL_LIMIT", "unbounded")
    reset_model_calls()
    with pytest.raises(ModelCallBudgetExceeded, match="positive integer"):
        note_model_call()
    assert model_calls() == 0


def test_targeted_action_tag_runner_pins_one_bounded_campaign():
    runner = (Path(__file__).resolve().parents[1] / "tools" /
              "run_live_action_tags.sh").read_text()
    assert "MOXIE_MODEL_CALL_LIMIT=6" in runner
    assert "timeout --foreground --kill-after=5s 360s" in runner
    assert runner.count("test_live_action_tags.py::") == 2
    assert "test_a_tagged_live_turn_reaches_the_wire" not in runner


def test_non_transient_error_not_retried():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise ValueError("bad")
    with pytest.raises(ValueError):
        call_with_backoff(boom, sleep=lambda s: None)
    assert calls["n"] == 1                               # no retry on a non-transient error


def test_pacer_grows_on_limit_and_decays_on_success():
    p = Pacer(grow=2.0, decay=0.5, sleep=lambda s: None, clock=lambda: 0.0)
    assert p.min_gap == 0.0
    p.on_rate_limit(); p.on_rate_limit()
    assert p.min_gap >= 1.0                              # grew while throttled
    hi = p.min_gap
    p.on_success()
    assert p.min_gap < hi                                # decays as the server recovers


def test_pacer_waits_before_request_when_throttled():
    slept = []
    p = Pacer(sleep=lambda s: slept.append(s), clock=lambda: 0.0)
    p.on_rate_limit()                                    # sets a gap, last=0
    p.before_request()                                   # clock still 0 → must wait
    assert slept and slept[0] > 0


def test_contentapp_rate_limit_gives_gentle_line_not_failure():
    def throttled(messages):
        raise _RateLimit()
    module = load_module({"conversations": [{"module_id": "CHAT", "content_id": "d",
                                             "prompt": "hi"}]})
    app = ContentApp(module, throttled)
    robot = RobotContext(device_id="d", child=ChildProfile(), module_id="CHAT", content_id="d")
    reply = app.respond(Turn(robot=robot, speech="hello"))
    assert reply.result_code is ResultCode.SUCCESS       # not a hard fail
    assert "second" in reply.text.lower()                # a gentle "one moment"
