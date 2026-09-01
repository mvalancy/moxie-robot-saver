"""
Live end-to-end test against our LiteLLM gateway — the real AI seam.

Runs ONLY when a gateway key is available (MOXIE_LLM_API_KEY / LITELLM_MASTER_KEY,
e.g. from mqtt/.env); skips cleanly otherwise, so CI (no key) stays green while the
build loops verify a real LLM turn when the key is present. Never commits a key.
"""
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))


def _load_dotenv():
    """Best-effort load of mqtt/.env (git-ignored) so a local key is picked up."""
    path = os.path.join(REPO, "mqtt", ".env")
    try:
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


_load_dotenv()
KEY = os.environ.get("MOXIE_LLM_API_KEY") or os.environ.get("LITELLM_MASTER_KEY") or ""
BASE = os.environ.get("MOXIE_LLM_BASE_URL", "https://gateway.graphlings.net/v1")
MODEL = os.environ.get("MOXIE_LLM_MODEL", "graphling-medium")

pytestmark = pytest.mark.skipif(
    not KEY, reason="no gateway key (set MOXIE_LLM_API_KEY in mqtt/.env for live LLM tests)")


def test_gateway_chat_returns_text():
    try:
        from moxie_sdk.chat import make_openai_chat
    except Exception as e:  # openai package not installed in this env
        pytest.skip(f"openai SDK unavailable: {e}")
    chat = make_openai_chat(BASE, KEY, MODEL, max_tokens=32, temperature=0)
    out = chat([{"role": "system", "content": "Reply with exactly: PONG"},
                {"role": "user", "content": "ping"}])
    assert isinstance(out, str) and out.strip(), "empty reply from gateway"


def test_content_module_turn_against_gateway():
    """A shipped content module producing a real reply via the live gateway."""
    try:
        from moxie_sdk.chat import make_openai_chat
        from moxie_sdk.content import load_modules, ContentApp
    except Exception as e:
        pytest.skip(f"SDK/openai unavailable: {e}")
    import json
    from moxie_sdk.types import Turn, RobotContext, ChildProfile
    with open(os.path.join(REPO, "mqtt", "content_modules", "starter.json")) as fh:
        module = load_modules(json.load(fh))
    app = ContentApp(module, make_openai_chat(BASE, KEY, MODEL, max_tokens=48))
    robot = RobotContext(device_id="d_live", child=ChildProfile(nickname="Sam"),
                         module_id="FREE_CHAT", content_id="default")
    reply = app.respond(Turn(robot=robot, speech="What's your favorite color?"))
    assert reply.text.strip(), "content module produced no reply from the gateway"
