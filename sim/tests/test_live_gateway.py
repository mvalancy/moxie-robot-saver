"""
Live end-to-end test against our LiteLLM gateway — the real AI seam.

Runs ONLY when a gateway key is available (MOXIE_LLM_API_KEY / LITELLM_MASTER_KEY,
e.g. from mqtt/.env); skips cleanly otherwise, so CI (no key) stays green while the
build loops verify a real LLM turn when the key is present. Never commits a key.
"""
import contextlib
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.dirname(__file__))

from helpers_runtime import load_repo_dotenv  # noqa: E402

# Finds mqtt/.env in this tree or in the main checkout, so the live tier also runs
# from a `git worktree` (where the git-ignored .env does not exist).
load_repo_dotenv()
KEY = os.environ.get("MOXIE_LLM_API_KEY") or os.environ.get("LITELLM_MASTER_KEY") or ""
BASE = os.environ.get("MOXIE_LLM_BASE_URL", "https://gateway.graphlings.net/v1")
MODEL = os.environ.get("MOXIE_LLM_MODEL", "graphling-medium")

pytestmark = pytest.mark.skipif(
    not KEY, reason="no gateway key (set MOXIE_LLM_API_KEY in mqtt/.env for live LLM tests)")


def test_gateway_chat_returns_text():
    """The bare gateway call: when the assembled stack below fails, this says whether the
    gateway itself answers. (Content-module and runtime-only variants were subsumed by it.)"""
    try:
        from moxie_sdk.chat import make_openai_chat
    except Exception as e:  # openai package not installed in this env
        pytest.skip(f"openai SDK unavailable: {e}")
    chat = make_openai_chat(BASE, KEY, MODEL, max_tokens=32, temperature=0)
    out = chat([{"role": "system", "content": "Reply with exactly: PONG"},
                {"role": "user", "content": "ping"}])
    assert isinstance(out, str) and out.strip(), "empty reply from gateway"


#: What this file sets to assemble a production-shaped appliance, and so must put back:
#: both are ENGINE SELECTORS a later `importlib.reload(config)` reads — `MOXIE_STT=off`
#: pins the ears, `MOXIE_APP=content` pins the brain. A leak only bites the NEXT live suite
#: in a credentialed full run (see `test_env_hygiene_live_suites.py`).
_ASSEMBLY_ENV = {"MOXIE_APP": "content", "MOXIE_STT": "off"}


@contextlib.contextmanager
def _assembly_env():
    """`_ASSEMBLY_ENV` for the duration, and the process left exactly as we found it —
    even when the body raises. Not `monkeypatch`: the values must survive an
    `importlib.reload(config)` inside the test."""
    keep = {k: os.environ.get(k) for k in _ASSEMBLY_ENV}
    os.environ.update(_ASSEMBLY_ENV)
    try:
        yield
    finally:
        for k, v in keep.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        # The body reloads `config`, so putting the ENVIRONMENT back is only half of it —
        # `config`'s module constants would still hold ours until somebody reloaded it
        # again. Reload it here, against the restored environment, so the process is left
        # consistent for a suite that reads `config` without reloading it first.
        if "config" in sys.modules:
            import importlib
            try:
                importlib.reload(sys.modules["config"])
            except Exception:          # a reload that cannot happen is not this test's
                pass                   # failure, and must not mask the body's result


def test_live_assembled_stack_end_to_end():
    """The real production path: config(MOXIE_APP=content) → run.assemble() → runtime →
    a live turn on the gateway → spec response. As close to `python run.py` as a test
    gets (config + assembly + runtime + live brain), minus the broker."""
    with _assembly_env():
        _live_assembled_stack_end_to_end()


def _live_assembled_stack_end_to_end():
    try:
        import importlib
        import json
        mqtt_dir = os.path.join(REPO, "mqtt")
        sys.path.insert(0, mqtt_dir)
        sys.path.insert(0, os.path.join(mqtt_dir, "supervisor"))
        import config as cfg
        importlib.reload(cfg)
        import run
        importlib.reload(run)
        from moxie_sdk.types import RobotContext, ChildProfile  # noqa: F401
    except Exception as e:
        pytest.skip(f"assembly/openai unavailable: {e}")

    class _FakeClient:
        def __init__(self):
            self.published = []

        def publish(self, topic, payload):
            self.published.append((topic, json.loads(payload)))

    rt = run.assemble(cfg)
    rt.client = _FakeClient()
    did = "d_asm"
    rt.robots[did] = RobotContext(device_id=did, child=rt.child,
                                  module_id="FREE_CHAT", content_id="default")
    rt._on_remote_chat(did, rt.robots[did], json.dumps(
        {"command": "prompt", "event_id": "e", "speech": "Tell me a fun fact about the moon."}))
    rt._pool.shutdown(wait=True)
    msgs = [p for (t, p) in rt.client.published if t.endswith("/commands/remote_chat")]
    assert msgs, "assembled stack published no remote_chat"
    assert msgs[-1]["result"] == "SUCCESS"
    assert msgs[-1]["output"]["text"].strip(), "empty reply from the assembled live stack"
