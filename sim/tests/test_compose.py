"""
One-command-stack tests (M7 / DoD criterion 5) — assert the repo-root docker-compose.yml
declares the stack we document: the three long-running services plus the cert one-shot,
healthchecks on all three, a restart policy, named volumes for every piece of persistent
state, and profiles that are strictly opt-in.

The second half is PARITY: the repo ships the same appliance twice (build-from-clone
`docker-compose.yml` and self-contained `docker-compose.images.yml`), and the second one
copies what it cannot reference. Those copies drift — see helpers_compose.py for the
v0.6.0 promotion that a drifted copy stalled. Until now only the deep tier's PR-to-main
docker smokes could see it; these guards see it in milliseconds on every PR.

Hermetic: parses YAML, never talks to Docker. The end-to-end proof (build → up → virtual
robot round-trip → fleet view → down -v) is sim/run_compose_smoke.sh.
"""
import os
import re
import sys

import pytest

yaml = pytest.importorskip("yaml")

sys.path.insert(0, os.path.dirname(__file__))

from helpers_compose import (BUILD_ONLY_KNOBS, IMAGE_ONLY_KNOBS,   # noqa: E402
                             PROFILE_ONLY_KNOBS, broker_conf_drift, env_file_keys,
                             env_parity, inlined_broker_conf, interpolated, moxie_env,
                             shape_parity, unescaped_dollars)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMPOSE_PATH = os.path.join(REPO, "docker-compose.yml")
IMAGES_PATH = os.path.join(REPO, "docker-compose.images.yml")
BROKER_CONF = os.path.join(REPO, "mqtt", "broker", "compose-mosquitto.conf")
ENV_EXAMPLE = os.path.join(REPO, ".env.example")
SMOKE_ENV = os.path.join(REPO, "sim", "compose-smoke.env")

CORE = ("broker", "supervisor", "console")
#: Every service an owner gets from `up` with no flags, either way in.
SHARED = ("certs",) + CORE

CLONE = "docker-compose.yml"
IMAGES = "docker-compose.images.yml"
#: Knobs that legitimately live in one file only (each claim is re-verified below).
SINGLE_FILE = BUILD_ONLY_KNOBS | IMAGE_ONLY_KNOBS | PROFILE_ONLY_KNOBS


@pytest.fixture(scope="module")
def compose():
    with open(COMPOSE_PATH) as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def raw():
    with open(COMPOSE_PATH) as fh:
        return fh.read()


@pytest.fixture(scope="module")
def images():
    with open(IMAGES_PATH) as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def images_raw():
    with open(IMAGES_PATH) as fh:
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


# ====================================================================================
# PARITY — docker-compose.yml  ⇄  docker-compose.images.yml
#
# The images file is what an owner downloads on its own, so it cannot reference
# anything in this repo: it repeats the supervisor's whole environment block and
# inlines the broker config. Every check below asserts one of those copies is still a
# copy. See helpers_compose.py for the bug that motivated them.
# ====================================================================================

# ---- environment parity ------------------------------------------------------------

def test_supervisor_env_parity(compose, images):
    """The exact bug that stalled v0.6.0: `MOXIE_ALLOW_UNVERIFIED_BOTS` was added to the
    clone compose by the PR that closed the pairing gate, and the images compose — written
    in parallel — never got it. Both branches' own smokes were green; the prebuilt-image
    stack came up refusing to pair."""
    problems = env_parity(compose, images, "supervisor",
                          a_name=CLONE, b_name=IMAGES, ignore=SINGLE_FILE)
    assert not problems, "supervisor env has drifted:\n  " + "\n  ".join(problems)


def test_console_env_parity(compose, images):
    problems = env_parity(compose, images, "console",
                          a_name=CLONE, b_name=IMAGES, ignore=SINGLE_FILE)
    assert not problems, "console env has drifted:\n  " + "\n  ".join(problems)


def test_certs_env_parity(compose, images):
    """The cert one-shot bakes MOXIE_BROKER_HOST into the broker cert's SAN — a robot
    cannot connect to a stack whose cert names the wrong host."""
    problems = env_parity(compose, images, "certs",
                          a_name=CLONE, b_name=IMAGES, ignore=SINGLE_FILE)
    assert not problems, "certs env has drifted:\n  " + "\n  ".join(problems)


def test_pairing_gate_knob_reaches_both_stacks(compose, images):
    """Named regression for the v0.6.0 promotion. Redundant with the parity check above
    on purpose: this one names the knob, so a future deletion says what broke."""
    key = "MOXIE_ALLOW_UNVERIFIED_BOTS"
    for name, doc in ((CLONE, compose), (IMAGES, images)):
        env = moxie_env(doc, "supervisor")
        assert key in env, (
            f"{name} does not forward {key} to the supervisor — a robot on that stack "
            f"would sit in pairing_status='unpairing' whatever the owner puts in .env")
        assert env[key] == "${%s:-}" % key, \
            f"{name} must pass {key} through with an EMPTY default (the gate stays CLOSED)"


def test_single_file_knobs_are_really_single_file(raw, images_raw, compose, images):
    """The parity checks above exclude a short allowlist. An allowlist that is not itself
    checked is a mute button, so re-prove each claim."""
    for knob in BUILD_ONLY_KNOBS:
        assert knob in raw, f"{knob} is allowlisted as build-time but {CLONE} never uses it"
        assert knob not in images_raw, \
            f"{knob} appears in {IMAGES} — a prebuilt image cannot have wheels added"
        for name, doc in ((CLONE, compose), (IMAGES, images)):
            assert knob not in "".join(moxie_env(doc, "supervisor").values()), \
                f"{knob} is in {name}'s supervisor RUNTIME env — it is a build arg"
    for knob in IMAGE_ONLY_KNOBS:
        assert knob in images_raw, f"{knob} is allowlisted as registry-only but {IMAGES} never uses it"
        assert knob not in raw, f"{knob} appears in {CLONE}, which builds rather than pulls"
    for knob in PROFILE_ONLY_KNOBS:
        assert knob in raw and knob not in images_raw, \
            f"{knob} is allowlisted to the clone-only voice/stt profiles"


# ---- the inlined broker config -----------------------------------------------------

def test_inlined_broker_config_matches_the_file(images):
    """`sim/run_compose_smoke.sh` makes this same assertion at runtime by slicing the
    literal block by indentation (it must stay dependency-free). This reads it through
    PyYAML's own folding instead — two independent readings, one truth — and runs on
    every PR instead of only in the deep tier."""
    with open(BROKER_CONF) as fh:
        drift = broker_conf_drift(images, fh.read(), inline_name=IMAGES,
                                  file_name="mqtt/broker/compose-mosquitto.conf")
    assert not drift, "the inlined broker config has DRIFTED:\n" + drift


def test_images_compose_stands_alone(images, images_raw):
    """Its whole promise is `curl` one file and `up`. A `configs: file:` entry or a bind
    mount would break that for an owner who never cloned."""
    entry = images["configs"]["mosquitto-conf"]
    assert "content" in entry and "file" not in entry, \
        f"{IMAGES} must INLINE the broker config, not reference a path"
    assert inlined_broker_conf(images), "the inlined broker config is empty"
    for name, svc in images["services"].items():
        for mount in svc.get("volumes") or []:
            source = str(mount).split(":", 1)[0]
            assert not (source.startswith(".") or source.startswith("/")), \
                f"{IMAGES} service {name} bind-mounts {source!r} — that file is not there"
        assert "build" not in svc, f"{IMAGES} service {name} has a build: — it must pull"


def test_broker_gets_its_config_both_ways(compose, images):
    """Same file, two delivery mechanisms, same container path. (The two ACL files
    beside it get the same treatment — test_broker_gets_its_acls_both_ways.)"""
    mounts = [m for m in compose["services"]["broker"]["volumes"]
              if "compose-mosquitto.conf" in m]
    assert mounts == ["./mqtt/broker/compose-mosquitto.conf:/mosquitto/config/mosquitto.conf:ro"]
    assert images["services"]["broker"]["configs"][0] == \
        {"source": "mosquitto-conf", "target": "/mosquitto/config/mosquitto.conf"}


# ---- service / healthcheck / port / volume parity ----------------------------------

def test_core_service_shape_parity(compose, images):
    """Same services, same healthchecks, same startup gating, same published-port
    defaults, same state locations — whichever file you brought the stack up with."""
    problems = shape_parity(compose, images, SHARED, a_name=CLONE, b_name=IMAGES)
    assert not problems, "the two compose files describe different stacks:\n  " + \
        "\n  ".join(problems)


def test_images_compose_declares_no_profile_services(images):
    """`voice` / `stt` bake extra wheels into the supervisor IMAGE, so they only exist on
    the build-from-clone path. If one ever appears here it must appear in SHARED too."""
    assert set(images["services"]) == set(SHARED), \
        f"{IMAGES} services changed: {sorted(set(images['services']) ^ set(SHARED))}"
    for name, svc in images["services"].items():
        assert not svc.get("profiles"), f"{name} is profile-gated in {IMAGES}"


def test_declared_volumes_match(compose, images):
    """Both files use `name: moxie`, so they SHARE the named volumes — an owner can switch
    paths without losing certs, the DB or Moxie's memory. That only holds if both declare
    the same set."""
    assert set(compose["volumes"]) == set(images["volumes"])
    assert compose.get("name") == images.get("name") == "moxie", \
        "the two files must share a project name or they would not share volumes"


# ---- documentation parity ----------------------------------------------------------

#: Forwarded knobs .env.example does not yet document. Empty today — the escape hatch
#: exists so a genuine gap can be recorded here (with a reason) instead of reddening dev.
KNOWN_UNDOCUMENTED = frozenset()

#: Documented knobs that reach the supervisor only through `env_file: .env`, not through
#: an explicit `environment:` passthrough. MOXIE_AUTOMARKUP is the markup floor's
#: one-variable rollback (mqtt/moxie_sdk/automarkup.py) and MOXIE_EXPRESSIVE the behavior
#: planner's (mqtt/supervisor/markup.py, planner|floor|off): both files behave identically,
#: so neither is a parity bug — but they are invisible to `docker compose config`.
KNOWN_ENV_FILE_ONLY = frozenset({"MOXIE_AUTOMARKUP", "MOXIE_EXPRESSIVE"})


@pytest.mark.parametrize("name,path", [(CLONE, COMPOSE_PATH), (IMAGES, IMAGES_PATH)])
def test_every_forwarded_knob_is_documented(name, path):
    """An owner can only set a knob they can find. `.env.example` is the only place they
    look, and it is the file the images path tells them to download beside the compose."""
    with open(path) as fh:
        used = interpolated(fh.read())
    with open(ENV_EXAMPLE) as fh:
        documented = env_file_keys(fh.read())
    missing = used - documented - KNOWN_UNDOCUMENTED
    assert not missing, \
        f".env.example does not document what {name} forwards: {sorted(missing)}"


def test_documented_knobs_reach_the_supervisor(raw, images_raw):
    """The other direction: a knob documented in .env.example that neither file
    interpolates is a knob an owner sets and nothing reads (unless env_file carries it —
    see KNOWN_ENV_FILE_ONLY)."""
    with open(ENV_EXAMPLE) as fh:
        documented = env_file_keys(fh.read())
    unwired = documented - interpolated(raw) - interpolated(images_raw)
    assert not unwired - KNOWN_ENV_FILE_ONLY, \
        f".env.example documents knobs no compose file forwards: {sorted(unwired)}"
    assert KNOWN_ENV_FILE_ONLY <= documented, \
        f"stale KNOWN_ENV_FILE_ONLY entries: {sorted(KNOWN_ENV_FILE_ONLY - documented)}"


def test_inlined_config_escapes_every_literal_dollar(images):
    """`broker_conf_drift` (and the runtime guard in sim/run_compose_smoke.sh) normalize
    `$$` → `$` before comparing, so a config that FORGOT to double its `$` would compare
    equal while docker compose silently substituted an empty variable — the broker would
    watch `SYS/broker/log` instead of `$SYS/broker/log` and the supervisor would never
    notice a robot connecting."""
    offenders = unescaped_dollars(images)
    assert not offenders, \
        "inlined broker config has an un-escaped `$` (write it `$$`):\n  " + \
        "\n  ".join(offenders)


# ====================================================================================
# NEGATIVE TESTS — prove the guards above BITE.
#
# A parity guard that only ever passes is indistinguishable from one that always
# passes. Each case below is a tiny in-memory compose pair with exactly one injected
# drift, and asserts the guard reports it AND names the key and the file at fault.
# ====================================================================================

def _doc(body):
    return yaml.safe_load(body)


_SUP_A = """
services:
  supervisor:
    environment:
      MOXIE_APP: ${MOXIE_APP:-content}
      MOXIE_ALLOW_UNVERIFIED_BOTS: ${MOXIE_ALLOW_UNVERIFIED_BOTS:-}
      MOXIE_BRAIN_BUDGET_S: ${MOXIE_BRAIN_BUDGET_S:-6}
      MOXIE_MQTT_HOST: broker
"""

# The v0.6.0 bug, in miniature: the images side simply never got the line.
_SUP_MISSING = """
services:
  supervisor:
    environment:
      MOXIE_APP: ${MOXIE_APP:-content}
      MOXIE_BRAIN_BUDGET_S: ${MOXIE_BRAIN_BUDGET_S:-6}
      MOXIE_MQTT_HOST: broker
"""

_SUP_DEFAULT_DRIFT = _SUP_A.replace("MOXIE_BRAIN_BUDGET_S:-6", "MOXIE_BRAIN_BUDGET_S:-30")
_SUP_LITERAL_DRIFT = _SUP_A.replace("MOXIE_MQTT_HOST: broker", "MOXIE_MQTT_HOST: mqtt")
_SUP_LOST_DEFAULT = _SUP_A.replace("${MOXIE_APP:-content}", "${MOXIE_APP}")
_SUP_EXTRA = _SUP_A + "      MOXIE_NEW_KNOB: ${MOXIE_NEW_KNOB:-}\n"


@pytest.mark.parametrize("label,body,expected", [
    ("the images file never got the key (the v0.6.0 bug)", _SUP_MISSING,
     ["MOXIE_ALLOW_UNVERIFIED_BOTS", "MISSING from " + IMAGES]),
    ("a default drifted", _SUP_DEFAULT_DRIFT,
     ["MOXIE_BRAIN_BUDGET_S", "DEFAULT differs", "'6'", "'30'"]),
    ("a literal drifted", _SUP_LITERAL_DRIFT,
     ["MOXIE_MQTT_HOST", "differs", "'broker'", "'mqtt'"]),
    ("a passthrough lost its default (now a REQUIRED variable)", _SUP_LOST_DEFAULT,
     ["MOXIE_APP", "DEFAULT differs", "'content'", "None"]),
    ("only the images file forwards a new knob", _SUP_EXTRA,
     ["MOXIE_NEW_KNOB", "MISSING from " + CLONE]),
])
def test_env_parity_guard_bites(label, body, expected):
    problems = env_parity(_doc(_SUP_A), _doc(body), "supervisor",
                          a_name=CLONE, b_name=IMAGES)
    assert problems, f"the guard did NOT catch: {label}"
    blob = "\n".join(problems)
    for fragment in expected:
        assert fragment in blob, f"{label}: message never mentions {fragment!r} — {blob}"


def test_env_parity_passes_when_identical():
    """The control: without an injected drift the same guard says nothing."""
    assert env_parity(_doc(_SUP_A), _doc(_SUP_A), "supervisor",
                      a_name=CLONE, b_name=IMAGES) == []


_CONF = "listener 1883\nallow_anonymous true\n# watch $SYS/broker/log\n"


def _images_with_conf(content, indent="      "):
    body = "".join(indent + line + "\n" for line in content.splitlines())
    return _doc("configs:\n  mosquitto-conf:\n    content: |\n" + body)


@pytest.mark.parametrize("label,inlined,expected", [
    ("a line was edited on disk but not in the copy",
     "listener 1883\nallow_anonymous false\n# watch $$SYS/broker/log\n",
     ["-allow_anonymous true", "+allow_anonymous false"]),
    ("a whole line is missing from the copy",
     "listener 1883\n# watch $$SYS/broker/log\n",
     ["-allow_anonymous true"]),
    ("the copy grew a line the file does not have",
     "listener 1883\nallow_anonymous true\nlistener 9001\n# watch $$SYS/broker/log\n",
     ["+listener 9001"]),
])
def test_broker_conf_guard_bites(label, inlined, expected):
    drift = broker_conf_drift(_images_with_conf(inlined), _CONF,
                              inline_name=IMAGES, file_name="compose-mosquitto.conf")
    assert drift, f"the guard did NOT catch: {label}"
    for fragment in expected:
        assert fragment in drift, f"{label}: diff never shows {fragment!r} — {drift}"


def test_broker_conf_guard_passes_on_a_faithful_copy():
    """Control — and it proves the `$$` un-escaping is what makes them equal."""
    faithful = _CONF.replace("$SYS", "$$SYS") + "\n\n"      # + trailing blank lines
    assert broker_conf_drift(_images_with_conf(faithful), _CONF) == ""


def test_dollar_escape_guard_bites():
    """A copy that is byte-equal after normalization but wrong in the file: compose would
    interpolate `$SYS` to nothing. The diff guard alone cannot see this."""
    sloppy = _images_with_conf(_CONF)                       # `$SYS`, not `$$SYS`
    assert broker_conf_drift(sloppy, _CONF) == "", "precondition: the diff guard is blind here"
    assert unescaped_dollars(sloppy) == ["# watch $SYS/broker/log"]
    assert unescaped_dollars(_images_with_conf(_CONF.replace("$SYS", "$$SYS"))) == []


_SHAPE_A = """
services:
  broker:
    depends_on: {certs: {condition: service_completed_successfully}}
    ports: ["${MOXIE_BIND_HOST:-0.0.0.0}:${MOXIE_PORT_MQTT:-1883}:1883"]
    healthcheck: {test: ["CMD-SHELL", "mosquitto_pub -h 127.0.0.1 -p 1883 -t hc -m up"]}
    volumes: ["moxie-broker-data:/mosquitto/data"]
    restart: unless-stopped
  console:
    depends_on: {supervisor: {condition: service_healthy}}
    ports: ["${MOXIE_BIND_HOST:-0.0.0.0}:${MOXIE_PORT_CONSOLE:-8080}:8080"]
    volumes: ["moxie-console-data:/data"]
    restart: unless-stopped
"""

_SHAPE_NO_CONSOLE = _SHAPE_A[:_SHAPE_A.index("  console:")]
_SHAPE_NO_HEALTHCHECK = "\n".join(
    l for l in _SHAPE_A.splitlines() if "healthcheck" not in l) + "\n"
_SHAPE_WEAK_DEPENDS = _SHAPE_A.replace("condition: service_healthy",
                                       "condition: service_started")
_SHAPE_PORT_DRIFT = _SHAPE_A.replace("MOXIE_PORT_CONSOLE:-8080", "MOXIE_PORT_CONSOLE:-8081")
_SHAPE_VOLUME_DRIFT = _SHAPE_A.replace("moxie-console-data:/data",
                                       "moxie-console-data:/var/lib/moxie")
_SHAPE_NO_RESTART = _SHAPE_A.replace("    restart: unless-stopped\n", "", 1)


@pytest.mark.parametrize("label,body,expected", [
    ("a service is missing entirely", _SHAPE_NO_CONSOLE,
     ["'console'", "MISSING from " + IMAGES]),
    ("the broker lost its healthcheck (the smoke would wait on nothing)",
     _SHAPE_NO_HEALTHCHECK, ["broker", "inline healthcheck"]),
    ("startup gating weakened to service_started", _SHAPE_WEAK_DEPENDS,
     ["console", "depends_on", "service_started"]),
    ("a published-port default drifted", _SHAPE_PORT_DRIFT,
     ["console", "published ports", "8081"]),
    ("state would land somewhere else in the container", _SHAPE_VOLUME_DRIFT,
     ["console", "named-volume", "/var/lib/moxie"]),
    ("a long-running service stopped being restarted", _SHAPE_NO_RESTART,
     ["broker", "restart policy"]),
])
def test_shape_parity_guard_bites(label, body, expected):
    problems = shape_parity(_doc(_SHAPE_A), _doc(body), ("broker", "console"),
                            a_name=CLONE, b_name=IMAGES)
    assert problems, f"the guard did NOT catch: {label}"
    blob = "\n".join(problems)
    for fragment in expected:
        assert fragment in blob, f"{label}: message never mentions {fragment!r} — {blob}"


def test_shape_parity_passes_when_identical():
    assert shape_parity(_doc(_SHAPE_A), _doc(_SHAPE_A), ("broker", "console"),
                        a_name=CLONE, b_name=IMAGES) == []


# ====================================================================================
# BROKER HARDENING — security-broker-auth.md §2 (P0), row T8.
#
# The slice is three lines of broker config, two ACL files and one credential, and
# every one of them has to reach BOTH stacks or the prebuilt-image appliance quietly
# ships the open broker while the clone ships the closed one. That is exactly the
# v0.6.0 shape of bug the parity guards above exist for, so these name it directly.
# ====================================================================================

ACL_CONFIGS = (("mosquitto-acl", "acl"), ("mosquitto-acl-robot", "acl-robot"))


@pytest.mark.parametrize("config_name,filename", ACL_CONFIGS)
def test_inlined_acls_match_the_files(images, config_name, filename):
    """Same guard as the broker config, for the two ACLs the images file must also
    inline — it cannot bind-mount them, and an owner downloads that one file."""
    with open(os.path.join(REPO, "mqtt", "broker", filename)) as fh:
        drift = broker_conf_drift(images, fh.read(), inline_name=IMAGES,
                                  file_name=f"mqtt/broker/{filename}",
                                  config_name=config_name)
    assert not drift, f"the inlined {filename} has DRIFTED:\n" + drift


@pytest.mark.parametrize("config_name,_f", ACL_CONFIGS)
def test_inlined_acls_escape_every_literal_dollar(images, config_name, _f):
    """`acl` grants `topic read $SYS/#`. Written `$SYS` inside a compose `content:`
    block, docker substitutes an empty variable and the supervisor silently loses the
    connect watch — while the drift guard above, which normalizes `$$`, sees nothing."""
    offenders = unescaped_dollars(images, config_name)
    assert not offenders, \
        f"inlined {config_name} has an un-escaped `$` (write it `$$`):\n  " + \
        "\n  ".join(offenders)


def test_broker_gets_its_acls_both_ways(compose, images):
    """Same two files, two delivery mechanisms, same container paths."""
    mounts = [m for m in compose["services"]["broker"]["volumes"] if "/broker/acl" in m]
    assert mounts == ["./mqtt/broker/acl:/mosquitto/config/acl:ro",
                      "./mqtt/broker/acl-robot:/mosquitto/config/acl-robot:ro"]
    assert images["services"]["broker"]["configs"] == [
        {"source": "mosquitto-conf", "target": "/mosquitto/config/mosquitto.conf"},
        {"source": "mosquitto-acl", "target": "/mosquitto/config/acl"},
        {"source": "mosquitto-acl-robot", "target": "/mosquitto/config/acl-robot"},
    ]


def test_the_inlined_broker_config_actually_loads_the_acls(images):
    """Belt and braces: the drift guard proves the copy matches the file, this proves
    the file being copied is the hardened one. A revert that removed `acl_file` from
    both would otherwise pass every parity check in this module."""
    body = "\n".join(inlined_broker_conf(images))
    for directive in ("per_listener_settings true",
                      "acl_file /mosquitto/config/acl-robot",
                      "acl_file /mosquitto/config/acl",
                      "password_file /mosquitto/config/keys/passwd"):
        assert directive in body, f"the inlined broker config lost `{directive}`"


def test_the_supervisor_credential_reaches_both_stacks(compose, images):
    """Named regression, in the shape of `test_pairing_gate_knob_reaches_both_stacks`:
    a supervisor that cannot authenticate loses `$SYS/broker/log` and stops noticing
    robots connecting — on the stack whose compose file missed the line, only."""
    for name, doc in ((CLONE, compose), (IMAGES, images)):
        env = moxie_env(doc, "supervisor")
        assert env.get("MOXIE_MQTT_USER") == "${MOXIE_MQTT_USER:-supervisor}", name
        assert env.get("MOXIE_MQTT_PASSWORD_FILE") == \
            "${MOXIE_MQTT_PASSWORD_FILE:-/certs/supervisor.pass}", name
        mounts = [m for m in doc["services"]["supervisor"]["volumes"]
                  if m.startswith("moxie-certs:")]
        assert mounts == ["moxie-certs:/certs:ro"], \
            f"{name}: the supervisor cannot read the minted credential"


def test_the_password_is_never_a_compose_literal(raw, images_raw):
    """The secret is handed over as a FILE on purpose: an `environment:` value is
    visible to `docker inspect` and to anything that can read the container's /proc."""
    for name, text in ((CLONE, raw), (IMAGES, images_raw)):
        assert "MOXIE_MQTT_PASSWORD:" not in text, \
            f"{name} forwards the password itself — hand over the FILE instead"


def test_the_plain_listener_is_loopback_by_default(compose, images):
    """§2.4. 1883 is the one listener with a fleet-wide identity behind it, so it is not
    a LAN door unless an owner says so. 8883 (the robot) and 9001 (the browser UI) keep
    MOXIE_BIND_HOST — a robot and a phone are on the LAN by definition."""
    for name, doc in ((CLONE, compose), (IMAGES, images)):
        ports = doc["services"]["broker"]["ports"]
        plain = [p for p in ports if p.endswith(":1883")]
        assert plain == ["${MOXIE_BIND_HOST_PLAIN:-127.0.0.1}:${MOXIE_PORT_MQTT:-1883}:1883"], \
            f"{name}: the plain listener is not loopback-bound by default"
        for suffix in (":8883", ":9001"):
            assert [p for p in ports if p.endswith(suffix)][0].startswith(
                "${MOXIE_BIND_HOST:-0.0.0.0}"), f"{name}: {suffix} moved off MOXIE_BIND_HOST"


def test_the_certs_one_shot_still_owns_the_shared_volume(compose, images):
    """The credential is minted by the service that already mints the certs, into the
    volume both the broker and the supervisor already mount — which is what keeps
    `docker compose up` the whole install."""
    for name, doc in ((CLONE, compose), (IMAGES, images)):
        assert "moxie-certs:/certs" in (doc["services"]["certs"]["volumes"] or []), name
        assert "moxie-certs:/mosquitto/config/keys:ro" in \
            doc["services"]["broker"]["volumes"], name


def test_the_defaulted_voice_knobs_do_not_pin_the_pickers_engine(compose, images):
    """The 🎚️ picker reads `MOXIE_TTS`/`MOXIE_STT` as an engine PIN when they name an
    engine outright (`voice_settings.pin_for_env`). Both files ship a default for both
    knobs, so a default that pinned would quietly cut every `docker compose up`
    deployment's dropdown down to one engine — a coupling neither file can see.

    `tone` is a permission (the last rung under the gateway and Piper) and `auto` is the
    absence of a choice; both must keep pinning nothing.
    """
    sys.path.insert(0, os.path.join(REPO, "mqtt"))
    from moxie_sdk import voice_settings as vs
    for name, doc in ((CLONE, compose), (IMAGES, images)):
        env = doc["services"]["supervisor"]["environment"]
        for var, kind in (("MOXIE_TTS", vs.SPEECH), ("MOXIE_STT", vs.LISTENING)):
            m = re.match(r"^\$\{%s:-(.*)\}$" % var, str(env[var]))
            assert m, f"{name}: {var} lost its `${{VAR:-default}}` shape"
            assert vs.pin_for_env(kind, m.group(1)) == "", (
                f"{name}: the default {var}={m.group(1)!r} PINS the {kind} engine, so "
                f"the picker would offer one engine out of the box")
