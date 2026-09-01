"""
Rate-limit / backoff / pacing tests (the AI-seam resilience) — pure, no network.
A busy gateway should slow us down and recover, not fail the child.
"""
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.chat import (  # noqa: E402
    is_rate_limit_error, is_offline_error, is_server_error,
    call_with_backoff, Pacer,
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
