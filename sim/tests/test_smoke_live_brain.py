"""`sim/run_smoke.sh --live-brain` — the SIL smoke with the mock taken out of it.

DoD #1 wants "a child can talk to Moxie end to end, proven by a live scenario, not a
mock". The smoke ran broker + supervisor + TTS + virtual robot around `MOXIE_APP=echo`;
`test_live_gateway.py` ran the live brain minus the broker. `--live-brain` joins them.
These are the hermetic half (no key, gateway, docker or broker), holding the four
properties that make the live mode trustworthy rather than merely present:

1. the default is still the echo app (every existing caller and CI job is untouched);
2. the live mode SKIPS, loudly and with status 0, when there is no key;
3. the robot can actually tell an echoed reply from a model's;
4. the log the run prints cannot carry the key that produced it.

The live half is dispatched by `sim/ci/ci-deep.yml`, where a secret exists; nothing here
spends a gateway call.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SIM = os.path.join(REPO, "sim")
SMOKE = os.path.join(SIM, "run_smoke.sh")

sys.path.insert(0, SIM)
sys.path.insert(0, os.path.join(SIM, "tools"))


def _smoke_source() -> str:
    with open(SMOKE, encoding="utf-8") as fh:
        return fh.read()


def _run(args, extra_env=None, timeout=60):
    """`run_smoke.sh` with a guaranteed-keyless environment, unless told otherwise.
    The key is set EMPTY rather than deleted: the environment wins over a developer's
    `mqtt/.env` (`setdefault`), so this behaves the same with or without a local key."""
    env = dict(os.environ)
    env["MOXIE_LLM_API_KEY"] = ""
    env.update(extra_env or {})
    return subprocess.run(["bash", SMOKE, *args], cwd=REPO, env=env, timeout=timeout,
                          capture_output=True, text=True)


# --------------------------------------------------------------------------- #
# 1. The default did not move
# --------------------------------------------------------------------------- #
def test_the_default_smoke_still_boots_the_echo_app():
    """`SMOKE_APP` starts as `echo` and is what the supervisor is started with: every
    flagless caller (CI tiers, scenarios, compose smoke) keeps the zero-dependency echo
    app, and a live default would put a gateway call and a key into the fast tier."""
    src = _smoke_source()
    assert 'SMOKE_APP="echo"' in src, \
        "run_smoke.sh no longer defaults to the echo app"
    assert 'MOXIE_APP="$SMOKE_APP"' in src, \
        "the supervisor is no longer started with the brain SMOKE_APP names"
    assert "MOXIE_APP=echo " not in src, \
        "the old hard pin is back — `MOXIE_APP=echo` bypasses the SMOKE_APP lever"


# --------------------------------------------------------------------------- #
# 2. No key → SKIP, not FAIL
# --------------------------------------------------------------------------- #
def test_live_brain_skips_loudly_and_greenly_without_a_key():
    """Status 0, a visible reason, and nothing started — the `test_live_gateway.py`
    contract; a live path that reddens every keyless build gets deleted or ignored.
    "No broker started" is load-bearing: it keeps this hermetic and proves the gate fires
    before anything is spent."""
    r = _run(["--live-brain"])
    assert r.returncode == 0, f"a keyless --live-brain run must SKIP, not fail:\n{r.stdout}\n{r.stderr}"
    assert "SKIPPED" in r.stdout, f"the skip was silent:\n{r.stdout}"
    assert "MOXIE_LLM_API_KEY" in r.stdout, \
        "the skip must name the variable that was missing, or nobody can act on it"
    assert "NOTHING WAS PROVEN" in r.stdout, \
        "a skip that does not say it proved nothing is how a green tier starts lying"
    assert "── broker on" not in r.stdout, \
        "the gate fires too late — a keyless run started a broker before skipping"


def test_live_brain_refuses_to_be_combined_with_telehealth():
    """The puppet round-trip never consults a brain: the pair would spend a gateway call
    on a path that cannot observe it and report success."""
    r = _run(["--live-brain", "--telehealth"])
    assert r.returncode == 2, f"expected a refusal (status 2), got {r.returncode}:\n{r.stdout}"
    assert "mutually exclusive" in r.stdout
    assert "── broker on" not in r.stdout, "the refusal came after a broker was started"


def test_the_echo_app_cannot_be_asked_for_as_a_live_brain():
    """`MOXIE_SMOKE_APP=echo --live-brain` is a request to prove a mock with a mock."""
    r = _run(["--live-brain"], {"MOXIE_SMOKE_APP": "echo"})
    assert r.returncode == 2, f"expected a refusal (status 2), got {r.returncode}:\n{r.stdout}"
    assert "contradiction" in r.stdout


# --------------------------------------------------------------------------- #
# 3. The robot can tell an echo from a brain
# --------------------------------------------------------------------------- #
def test_the_robot_recognises_the_echo_app_s_own_answer():
    """`--reject-echo` is the whole difference from the ordinary smoke: without it a run
    silently fallen back to `echo` (mistyped `MOXIE_APP`, failed build, stale container)
    passes every other assertion and reports a live proof."""
    import virtual_moxie as vm
    assert vm.is_echo_reply("You said: hello Moxie", "hello Moxie")
    # Markup is stripped first: `MOXIE_EXPRESSIVE` can dress a reply on the way out, and
    # an echoed line wearing markup is still an echoed line.
    assert vm.is_echo_reply("<mark name='happy'/>You said: hello Moxie", "hello Moxie")
    assert vm.is_echo_reply("  You said: hello Moxie  ", "hello Moxie")


@pytest.mark.parametrize("reply", [
    "Hello there! I'm so happy to see you. How was your morning?",
    "Hi! What do you want to play today?",
    "",
    "You said: something else",
])
def test_the_robot_does_not_mistake_a_real_reply_for_the_echo_app(reply):
    """The check may not be so loose that a model's answer trips it. The echo app
    answers the prompt it was GIVEN, so quoting a different prompt is not this turn's echo."""
    import virtual_moxie as vm
    assert not vm.is_echo_reply(reply, "hello Moxie")


def test_the_smoke_asks_the_robot_to_reject_the_echo_only_when_live():
    src = _smoke_source()
    for needle in ('REJECT_ECHO="--reject-echo"', "$REJECT_ECHO", 'REJECT_ECHO=""'):
        assert needle in src, f"run_smoke.sh lost `{needle}` (live-only --reject-echo)"


# --------------------------------------------------------------------------- #
# 4. The key cannot reach the log the run prints
# --------------------------------------------------------------------------- #
def test_the_secret_checker_catches_a_planted_key_without_printing_it(tmp_path, capsys,
                                                                    monkeypatch):
    """The run copies a keyed child's log to a public build log, so the check runs BEFORE
    the tail is printed and names the variable, never its value (a guard that quoted the
    secret would be the leak)."""
    import assert_no_secret_in_log as guard
    secret = "sk-not-a-real-key-0123456789"
    log = tmp_path / "sup.log"
    log.write_text(f"[runtime] booted with key={secret}\n")
    monkeypatch.setenv("MOXIE_LLM_API_KEY", secret)
    assert guard.leaks(log.read_text()) == ["MOXIE_LLM_API_KEY"]
    rc = guard.main(["assert_no_secret_in_log.py", str(log)])
    assert rc == 1, "a log containing the key must fail the check"
    out = capsys.readouterr().out
    assert "MOXIE_LLM_API_KEY" in out, "the report must name the variable"
    assert secret not in out, "the guard printed the secret it caught"


def test_the_secret_checker_passes_a_clean_log_and_ignores_placeholders(tmp_path,
                                                                      monkeypatch):
    import assert_no_secret_in_log as guard
    log = tmp_path / "sup.log"
    log.write_text("[runtime] broker connected rc=Success\n[run] 🧠 brain: content\n")
    monkeypatch.setenv("MOXIE_LLM_API_KEY", "sk-not-a-real-key-0123456789")
    assert guard.leaks(log.read_text()) == []
    # a short placeholder must not arm the guard — `x` is in every log ever written
    monkeypatch.setenv("MOXIE_LLM_API_KEY", "x")
    assert guard.secrets_in_scope() == {}


def test_the_smoke_checks_the_log_before_it_prints_the_tail():
    """Order is the property. Checking after the tail is printed proves nothing."""
    src = _smoke_source()
    check = src.find("assert_no_secret_in_log.py")
    tail = src.find('echo "── supervisor log tail ──"')
    assert check != -1, "run_smoke.sh --live-brain no longer checks the log for the key"
    assert tail != -1, "the log tail line moved; update this guard"
    assert check < tail, \
        "the secret check runs AFTER the supervisor log has already been printed"


# --------------------------------------------------------------------------- #
# 5. The deep tier runs it, and only the deep tier
# --------------------------------------------------------------------------- #
#: The tier templates are the version-controlled source of truth; `.github/workflows/`
#: holds installed copies that `test_ci_workflows.py` proves byte-identical to them.
TIERS = os.path.join(SIM, "ci")
INVOCATION = "sim/run_smoke.sh --live-brain"


def _tier(name: str) -> str:
    with open(os.path.join(TIERS, name), encoding="utf-8") as fh:
        return fh.read()


def test_only_the_deep_tier_names_the_live_brain_smoke():
    """The fast tier must never spend a gateway call or need a secret (a key would become
    a merge precondition); live stages belong in the deep tier behind `workflow_dispatch`."""
    named = sorted(f for f in os.listdir(TIERS)
                   if f.endswith((".yml", ".yaml")) and INVOCATION in _tier(f))
    assert named == ["ci-deep.yml"], (
        f"`{INVOCATION}` should be named by the deep tier and nothing else, but the "
        f"tiers naming it are: {named or 'none — no tier runs it at all'}")


def test_the_deep_tier_runs_it_dispatch_only_and_refuses_a_silent_skip():
    """Dispatch-only (it spends a gateway call; forks get no secrets), and an explicit
    failure on the harness's SKIP line — in the tier that exists to produce the proof, a
    missing secret must not become a green run."""
    import yaml
    deep = yaml.safe_load(_tier("ci-deep.yml"))
    steps = [s for job in deep["jobs"].values() for s in (job.get("steps") or [])]
    live = [s for s in steps if INVOCATION in (s.get("run") or "")]
    assert len(live) == 1, f"expected exactly one live-brain step, found {len(live)}"
    step = live[0]
    assert "workflow_dispatch" in (step.get("if") or ""), \
        "the live-brain step is not dispatch-only — a fork PR or a push would reach it"
    assert "MOXIE_LLM_API_KEY" in (step.get("env") or {}), \
        "the step names no gateway secret, so it can only ever skip"
    assert "SKIPPED" in step["run"], \
        "the step does not fail on the harness's SKIP line — an absent secret would " \
        "report a green live proof that ran nothing"
