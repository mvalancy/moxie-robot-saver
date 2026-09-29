"""The brain endpoint is configuration, and an unset one is loud: it used to default to the
maintainer's gateway, silently pointing a stranger's supervisor at someone else's server.
The class-wide guard (no deployment hostname in shipped code) is `test_no_deployment_defaults.py`.
"""
from helpers_runtime import reload_config                      # noqa: E402
import os
import re
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MQTT = os.path.join(REPO, "mqtt")
sys.path.insert(0, MQTT)
sys.path.insert(0, os.path.join(MQTT, "supervisor"))

#: Everything that could make "nothing is configured" untrue for these tests.
_ENV = ("MOXIE_APP", "MOXIE_LLM_BASE_URL", "MOXIE_LLM_API_KEY", "LITELLM_MASTER_KEY",
        "MOXIE_LLM_MODEL", "MOXIE_CONTENT_MODULE", "MOXIE_WEBHOOK_ENDPOINT",
        "MOXIE_VOICE_BASE_URL", "MOXIE_VOICE_API_KEY", "MOXIE_STT_BASE_URL",
        "MOXIE_STT_API_KEY", "MOXIE_STT", "MOXIE_TTS", "MOXIE_PIPER_MODEL")

#: Hosts a URL may name and still be "nowhere in particular": this machine, or one of the
#: reserved-for-documentation names that resolve to nothing anywhere (RFC 2606/6761).
_NOWHERE = re.compile(
    r"^(?:127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\]|localhost|host\.docker\.internal"
    r"|(?:[A-Za-z0-9-]+\.)*(?:example|invalid|test|localhost)"
    r"|(?:[A-Za-z0-9-]+\.)*example\.(?:com|net|org))$")

_URL = re.compile(r"https?://([A-Za-z0-9_.\-\[\]:]+?)(?:[:/]|$)")


def _fresh(monkeypatch, **env):
    return reload_config(monkeypatch, _ENV, **env)


def _remote_hosts(text: str) -> list:
    """Every host in `text` that is somebody's actual deployment."""
    return [h for h in _URL.findall(str(text)) if not _NOWHERE.match(h)]


# ------------------------------------------------------- nothing points anywhere --

def test_an_unconfigured_supervisor_names_no_remote_host(monkeypatch):
    """With nothing set, no value this module exposes reaches out to anyone — a sweep of
    every exposed string, so a different vendor's host or a second variable with the same
    habit fails too."""
    c = _fresh(monkeypatch)
    offenders = {name: value
                 for name, value in vars(c).items()
                 if isinstance(value, str) and not name.startswith("__")
                 and _remote_hosts(value)}
    assert offenders == {}, \
        "an unconfigured supervisor points at somebody's deployment: " + repr(offenders)


def test_the_ears_do_not_inherit_an_endpoint_that_was_never_configured(monkeypatch):
    """`STT_BASE_URL` falls back to `VOICE_BASE_URL` and then to `LLM_BASE_URL`. While the
    brain had a default, that chain handed the *ears* a live endpoint on a box where
    nobody had configured one — a child's voice one missing `if` away from an upload."""
    c = _fresh(monkeypatch)
    assert c.STT_BASE_URL == ""
    from moxie_sdk.stt import OpenAITranscriber
    assert OpenAITranscriber.available(c.STT_BASE_URL) is False


# --------------------------------------------------------- the refusal is loud ----

@pytest.mark.parametrize("app", ["llm", "content"])
def test_an_app_that_needs_a_brain_refuses_to_guess_one(monkeypatch, app):
    """It exits NAMING the variable (tokens extracted, so wording may change), and the
    example URL it offers is loopback — an example URL is the shape the original defect took."""
    c = _fresh(monkeypatch, MOXIE_APP=app)
    with pytest.raises(SystemExit) as exc:
        c.build_app()
    named = set(re.findall(r"MOXIE_[A-Z0-9_]+", str(exc.value)))
    assert "MOXIE_LLM_BASE_URL" in named, \
        f"the refusal must name the variable to set; it named {sorted(named)}"
    assert "://" in str(exc.value) and _remote_hosts(str(exc.value)) == []


def test_the_refusal_arrives_at_assembly_not_on_the_first_turn(monkeypatch):
    """`run.assemble()` is where an operator is still watching the log. A brain that only
    failed when a child spoke would be discovered as a fuzzy reply, hours later."""
    from helpers_runtime import load_mqtt_run
    c = _fresh(monkeypatch, MOXIE_APP="llm")
    run = load_mqtt_run()
    with pytest.raises(SystemExit):
        run.assemble(c)


# ------------------------------------------------------- configured, and exact ----

def test_a_configured_endpoint_is_used_exactly(monkeypatch):
    """Nothing is substituted, appended or defaulted around what the operator set."""
    pytest.importorskip("openai", reason="LLMApp builds a real client")
    c = _fresh(monkeypatch, MOXIE_APP="llm",
               MOXIE_LLM_BASE_URL="http://127.0.0.1:11434/v1")
    app = c.build_app()
    assert str(app._client.base_url).rstrip("/") == "http://127.0.0.1:11434/v1"


def test_echo_still_needs_no_brain_at_all(monkeypatch):
    """The escape hatch the refusal advertises has to be real: it is what `run_smoke.sh`,
    `sim/compose-smoke.env` and `helpers_stack.Supervisor` all rely on to bring a
    supervisor up on a machine with no endpoint and no key."""
    c = _fresh(monkeypatch, MOXIE_APP="echo")
    assert c.build_app().name == "echo"


def test_webhook_still_needs_only_its_own_endpoint(monkeypatch):
    """The rule this one follows is the older sibling of `require_llm_base_url`, and it
    must not have acquired a brain requirement by accident."""
    c = _fresh(monkeypatch, MOXIE_APP="webhook",
               MOXIE_WEBHOOK_ENDPOINT="http://127.0.0.1:9/turn")
    assert c.build_app().name == "webhook"
