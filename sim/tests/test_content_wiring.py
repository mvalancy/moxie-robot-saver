"""
M2 wiring tests — the shipped example module runs through ContentApp, the templated
opener renders, and the AI-seam offline/soft-error handling behaves per ai-seam.md §2.
Pure (no openai/broker); runs in CI's pytest.
"""
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.content import load_modules, ContentApp  # noqa: E402
from moxie_sdk.types import Turn, RobotContext, ChildProfile, ResultCode  # noqa: E402
from moxie_sdk.chat import is_offline_error  # noqa: E402

STARTER = os.path.join(REPO, "mqtt", "content_modules", "starter.json")


def _app(chat):
    with open(STARTER) as fh:
        module = load_modules(json.load(fh))
    return ContentApp(module, chat, persona="P")


def _robot(nickname="Sam"):
    return RobotContext(device_id="d1", child=ChildProfile(nickname=nickname),
                        module_id="FREE_CHAT", content_id="default")


def test_shipped_module_is_valid_and_runs():
    app = _app(lambda messages: "Let's talk about dinosaurs!")
    reply = app.respond(Turn(robot=_robot(), speech="hi"))
    assert reply.text == "Let's talk about dinosaurs!"


def test_shipped_opener_renders_nickname():
    app = _app(lambda m: "x")
    g = app.greeting(_robot("Robin"))
    assert g is not None
    assert g.text == "Hi Robin! What do you want to talk about?"


def test_offline_endpoint_yields_error_offline():
    def dead(messages):
        raise ConnectionError("connection refused")
    reply = _app(dead).respond(Turn(robot=_robot(), speech="hi"))
    assert reply.result_code is ResultCode.ERROR_OFFLINE


def test_soft_error_keeps_talking():
    def boom(messages):
        raise ValueError("bad json from model")
    reply = _app(boom).respond(Turn(robot=_robot(), speech="hi"))
    assert reply.result_code is ResultCode.SUCCESS
    assert reply.text and "fuzzy" in reply.text.lower()


def test_is_offline_error_classification():
    assert is_offline_error(ConnectionError()) is True
    assert is_offline_error(TimeoutError()) is True
    assert is_offline_error(ValueError()) is False
