"""
One-command-stack tests (M7 / DoD criterion 5) — assert the repo-root docker-compose.yml
declares the stack we document: the three long-running services plus the cert one-shot,
healthchecks on all three, a restart policy, named volumes for every piece of persistent
state, and profiles that are strictly opt-in.

Hermetic: parses YAML, never talks to Docker. The end-to-end proof (build → up → virtual
robot round-trip → fleet view → down -v) is sim/run_compose_smoke.sh.
"""
import os
import re

import pytest

yaml = pytest.importorskip("yaml")

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMPOSE_PATH = os.path.join(REPO, "docker-compose.yml")
ENV_EXAMPLE = os.path.join(REPO, ".env.example")
SMOKE_ENV = os.path.join(REPO, "sim", "compose-smoke.env")

CORE = ("broker", "supervisor", "console")


@pytest.fixture(scope="module")
def compose():
    with open(COMPOSE_PATH) as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def raw():
    with open(COMPOSE_PATH) as fh:
        return fh.read()


def _env_keys(path):
    keys = set()
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                keys.add(line.split("=", 1)[0].strip())
    return keys


# ---- shape -------------------------------------------------------------------------

def test_project_name_and_services(compose):
    assert compose.get("name") == "moxie"
    for svc in ("certs",) + CORE:
        assert svc in compose["services"], f"missing service {svc}"


def test_core_services_have_no_profile(compose):
    """`docker compose up` with no flags must bring the whole stack up."""
    for svc in ("certs",) + CORE:
        assert not compose["services"][svc].get("profiles"), \
            f"{svc} is profile-gated — plain `docker compose up` would skip it"


def test_optional_profiles_are_opt_in(compose):
    for svc, profile in (("voice-model", "voice"), ("stt-model", "stt")):
        assert compose["services"][svc]["profiles"] == [profile]


def test_every_core_service_is_healthchecked(compose):
    """The smoke waits on these; a missing one would make it pass blind. A service may
    carry its healthcheck in compose (broker: upstream image) or in its own Dockerfile
    (supervisor, console) — both are honoured by `depends_on: service_healthy`."""
    for svc in CORE:
        hc = compose["services"][svc].get("healthcheck")
        if hc:
            assert hc.get("test"), f"{svc} healthcheck has no test"
            continue
        build = compose["services"][svc].get("build") or {}
        dockerfile = build.get("dockerfile") or os.path.join(build["context"], "Dockerfile")
        with open(os.path.join(REPO, dockerfile)) as fh:
            assert "HEALTHCHECK" in fh.read(), f"{svc} has no healthcheck anywhere"


def test_supervisor_healthcheck_hits_status(compose):
    assert compose["services"]["supervisor"]["build"]["context"] == "./mqtt"
    with open(os.path.join(REPO, "mqtt", "Dockerfile")) as fh:
        body = fh.read()
    hc = body[body.index("HEALTHCHECK"):]
    assert "/status" in hc, "the supervisor healthcheck must exercise /status"


def test_restart_policy(compose):
    for svc in CORE:
        assert compose["services"][svc].get("restart") == "unless-stopped"
    for svc in ("certs", "voice-model", "stt-model"):
        assert compose["services"][svc].get("restart") == "no", \
            f"{svc} is a one-shot; it must not be restarted"


def test_startup_order_is_gated_on_health(compose):
    assert compose["services"]["broker"]["depends_on"]["certs"]["condition"] == \
        "service_completed_successfully"
    assert compose["services"]["supervisor"]["depends_on"]["broker"]["condition"] == \
        "service_healthy"
    assert compose["services"]["console"]["depends_on"]["supervisor"]["condition"] == \
        "service_healthy"


# ---- persistence -------------------------------------------------------------------

def test_persistent_state_lives_in_named_volumes(compose):
    declared = set(compose["volumes"])
    for vol in ("moxie-certs", "moxie-console-data", "moxie-supervisor-data",
                "moxie-broker-data", "moxie-models", "moxie-whisper-cache"):
        assert vol in declared, f"volume {vol} not declared"
    sup = compose["services"]["supervisor"]
    assert any(v.startswith("moxie-supervisor-data:") for v in sup["volumes"])
    assert sup["environment"]["MOXIE_DATA_DIR"] == "/data"
    con = compose["services"]["console"]
    assert any(v.startswith("moxie-console-data:") for v in con["volumes"])
    assert con["environment"]["MOXIE_DB"].startswith("/data/")


def test_console_reads_the_supervisor_through_the_status_proxy(compose):
    """The runtime's status server is loopback-only, so the console must use the proxy
    port the supervisor entrypoint opens (mqtt/status_proxy.py), not 8930."""
    sup = compose["services"]["supervisor"]["environment"]
    assert sup["MOXIE_STATUS_PROXY_PORT"] == "8931"
    url = compose["services"]["console"]["environment"]["MOXIE_SUPERVISOR_STATUS"]
    assert url == "http://supervisor:8931/status"


# ---- config surface ----------------------------------------------------------------

def test_defaults_are_zero_dependency(compose):
    """No key, no model, no extra wheels needed for `docker compose up` to talk."""
    env = compose["services"]["supervisor"]["environment"]
    assert env["MOXIE_APP"].endswith(":-content}")
    assert env["MOXIE_TTS"].endswith(":-tone}"), "the built-in tone voice is the default"
    assert env["MOXIE_PIPER_MODEL"].endswith(":-}"), "a Piper model must NOT be assumed"
    assert compose["services"]["supervisor"]["build"]["args"]["EXTRAS"].endswith(":-}")


def test_every_interpolated_knob_is_documented(raw):
    used = set(re.findall(r"\$\{(MOXIE_[A-Z0-9_]+)", raw))
    documented = _env_keys(ENV_EXAMPLE)
    assert used - documented == set(), \
        f".env.example does not document: {sorted(used - documented)}"


def test_env_example_ships_no_secret():
    text = open(ENV_EXAMPLE).read()
    assert not re.search(r"sk-[A-Za-z0-9_]{12}", text), ".env.example must never hold a key"
    for line in text.splitlines():
        if line.startswith("MOXIE_LLM_API_KEY") or line.startswith("MOXIE_VOICE_API_KEY"):
            assert line.split("=", 1)[1].strip() == "", "API keys must ship empty"


def test_env_example_has_no_trailing_comments():
    """docker compose does NOT strip `KEY=value  # note` — the note becomes the value.
    A stray comment on MOXIE_PIPER_MODEL would make the supervisor try to load a Piper
    model called '# e.g. ...' and exit at startup."""
    for path in (ENV_EXAMPLE, SMOKE_ENV):
        for n, line in enumerate(open(path), 1):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            value = line.split("=", 1)[1]
            assert "#" not in value, f"{path}:{n} has a trailing comment: {line!r}"


def test_smoke_env_uses_free_ports():
    """The compose smoke must never bind the ports a real stack (or the SIL) uses."""
    defaults = {"MOXIE_PORT_MQTT": "1883", "MOXIE_PORT_MQTT_TLS": "8883",
                "MOXIE_PORT_WS": "9001", "MOXIE_PORT_CONSOLE": "8080",
                "MOXIE_PORT_STATUS": "8931"}
    smoke = {}
    for line in open(SMOKE_ENV):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            smoke[k.strip()] = v.strip()
    reserved = set(defaults.values()) | {"8930", "8932"}   # 8930/8932: bare-metal status
    for key, default in defaults.items():
        assert key in smoke, f"{SMOKE_ENV} must pin {key}"
        assert smoke[key] not in reserved, f"smoke {key}={smoke[key]} collides with a real stack"
    assert smoke.get("MOXIE_BIND_HOST") == "127.0.0.1", "the smoke must not publish to the LAN"
    assert smoke.get("MOXIE_LLM_API_KEY", "") == "", "the smoke must need no credentials"
