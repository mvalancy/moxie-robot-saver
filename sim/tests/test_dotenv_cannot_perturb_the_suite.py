"""
The fence around playbook rule 20, and the proof that the fence is load-bearing.

**Rule 20, restated.** `mqtt/.env` is git-ignored, so it exists in a developer's main
checkout and in no CI runner and no git worktree. `mqtt/config.py` loads it at import with
`os.environ.setdefault(...)`, so a test that simulates "nothing is configured" by deleting
a variable and reloading the module had it **refilled from the file**. Those tests assert
nothing on the one machine a human actually runs them on, and pass everywhere else — so
CI is green, the developer's suite is red, and the disagreement teaches people to
distrust the local suite rather than to read it.

**What this file adds, and why the existing fix was not enough.** The opt-out for rule 20
is `MOXIE_SKIP_DOTENV=1`, and it was applied inside each affected test helper. Measured
2026-09-05, that is too late to work:

  * `config._load_env` fills `os.environ` with `setdefault`, so the **first** `import
    config` anywhere in the session promotes every key in the file to a real environment
    variable, permanently. Nothing removes them. A later `MOXIE_SKIP_DOTENV=1` only stops
    the *file* being re-read — the values are no longer coming from the file — so the flag
    is a **first-import-wins** switch and every in-helper caller sets it after the race is
    already lost. `test_assemble.py` and `test_voice_settings.py` pass when run ALONE and
    fail in the full suite for exactly this reason: whether they assert anything depends
    on collection order.
  * the helpers then delete a hand-maintained **list** of names — nine in
    `test_assemble._fresh_config` against twenty-five documented in `mqtt/.env.example` —
    so a knob nobody remembered (`MOXIE_PIPER_MODEL`) still reached the code under test.

Both are properties of the *session*, not of any one test, so the fix is a single decision
taken before the first import, in `conftest.py`, where pytest guarantees to arrive before
it collects anything. This file is that decision's guard.

**How it proves it, rather than asserting it.** A throwaway dotenv is written here, into
`tmp_path`, and the suite is run against it in a subprocess in both configurations. With
the fence the run is green; without it the same run is **red**, which is what stops this
from being a test that would pass against a fence made of nothing. The mutation control is
the point: half two ("with the flag, the suite is clean") passes trivially in a worktree,
where there is no dotenv to be clean of — which is precisely the blind spot that hid the
defect for a day, so the red half is the half that means something.

**It never goes near a developer's own `mqtt/.env`.** That file is somebody's real
configuration containing a real key; nothing here reads it, writes it, moves it or asks
what is in it. The loader is pointed at this file's own fixture with `MOXIE_DOTENV`, which
is the seam `config.py` documents for exactly this purpose.

No network, no gateway, no key: the four credential variables are blanked for every
subprocess below, so the live tier inside the probe skips as it does on CI.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ENV_EXAMPLE = os.path.join(REPO, "mqtt", ".env.example")

#: The files the probe runs. Chosen because each one contains at least one test whose
#: whole claim is "nothing is configured" — the class rule 20 is about — and because
#: together they are ~2 s, which keeps this guard cheap enough that nobody deletes it.
_UNDER_TEST = ("test_assemble.py", "test_voice_settings.py",
               "test_device_permits.py", "test_stt_gateway.py")

#: A plausible developer's dotenv: every knob `mqtt/.env.example` documents, set to a
#: value that is *valid* rather than merely non-empty, so the probe fails the way a real
#: machine fails instead of by tripping over garbage. No real host and no real key —
#: `.invalid` is reserved by RFC 2606 and can never resolve.
#:
#: `test_the_fixture_covers_every_documented_knob` keeps this in step with `.env.example`.
#: That assertion is the denylist bug from the module docstring, inverted: the helpers this
#: guard replaces had to remember to *delete* each new knob and silently stopped covering
#: the ones they forgot, whereas a new knob here fails a test until it is listed.
_FIXTURE_ENV = {
    "MOXIE_APP": "llm",
    "MOXIE_CHILD_NICKNAME": "fixture-kid",
    "MOXIE_LLM_BASE_URL": "http://fixture.invalid/v1",
    "MOXIE_LLM_API_KEY": "fixture-not-a-real-key",
    "MOXIE_LLM_MODEL": "fixture-medium",
    "MOXIE_BRAIN_BUDGET_S": "7",
    "MOXIE_ALLOW_UNVERIFIED_BOTS": "1",
    "MOXIE_STREAMING": "1",
    "MOXIE_AUTOMARKUP": "1",
    "MOXIE_EXPRESSIVE": "planner",
    "MOXIE_CONTENT_MODULE": "FREE_CHAT",
    "MOXIE_BROKER_HOST": "127.0.0.1",
    "MOXIE_TTS": "gateway",
    "MOXIE_TTS_VOICE": "fixture-voice",
    "MOXIE_VOICE_BASE_URL": "http://fixture.invalid/v1",
    "MOXIE_VOICE_API_KEY": "fixture-not-a-real-key",
    "MOXIE_VOICE_MODEL": "fixture-tts",
    "MOXIE_VOICE_FORMAT": "wav",
    "MOXIE_VOICE_SAMPLE_RATE": "16000",
    "MOXIE_PIPER_MODEL": "/fixture/voice.onnx",
    "MOXIE_PIPER_CONFIG": "/fixture/voice.onnx.json",
    "MOXIE_STT": "gateway",
    "MOXIE_STT_MODEL": "fixture-stt",
    "MOXIE_STT_BASE_URL": "http://fixture.invalid/v1",
    "MOXIE_STT_API_KEY": "fixture-not-a-real-key",
}

#: Runs the files under test in a subprocess, having imported `config` FIRST. That import
#: is not incidental — it is the defect being reproduced. In a real full-suite run some
#: earlier module imports `config` before any helper sets the flag, and from that moment
#: the dotenv has already been copied into `os.environ`; doing it explicitly here makes a
#: whole-suite property deterministic instead of dependent on collection order.
_PROBE = """
import os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "mqtt"))
import config                      # the first import of the session — the polluting one
import pytest
sys.exit(int(pytest.main(["-q", "-p", "no:cacheprovider", *sys.argv[2:]])))
"""

#: Every variable a live suite reads to decide whether it has credentials. Blanked (not
#: deleted) for every subprocess here, because `setdefault` leaves an empty value alone —
#: so the probe can never reach a gateway, whatever the fixture says.
_CREDENTIALS = ("MOXIE_LLM_API_KEY", "LITELLM_MASTER_KEY",
                "MOXIE_VOICE_API_KEY", "MOXIE_STT_API_KEY")


def _write_fixture(tmp_path):
    """The throwaway dotenv, written where only this test can see it."""
    f = tmp_path / "fixture.env"
    f.write_text("# throwaway fixture written by a test — never a real deployment\n"
                 + "".join(f"{k}={v}\n" for k, v in _FIXTURE_ENV.items()))
    return f


def _run_probe(tmp_path, *, fenced):
    """Run the files under test against the fixture dotenv, with the fence on or off.

    `fenced=True` reproduces what `conftest.py` now arranges for every ordinary run;
    `fenced=False` is the state the suite was in before it, and must come back red.
    """
    runner = tmp_path / "run_probe.py"
    runner.write_text(_PROBE)
    # Start from an environment with NO `MOXIE_*` in it. Inheriting ours would make this
    # guard depend on what the rest of the session had already exported — and in a full
    # run that is a lot, because `helpers_runtime.load_repo_dotenv()` copies a whole
    # deployment's dotenv into `os.environ` at collection time for the live tier's
    # benefit. This guard is about the FILE, so the file has to be the only thing the
    # subprocess can be reacting to.
    env = {k: v for k, v in os.environ.items() if not k.startswith("MOXIE_")}
    env.update(MOXIE_DOTENV=str(_write_fixture(tmp_path)),
               **{k: "" for k in _CREDENTIALS})
    # Naming MOXIE_DOTENV is itself an opinion, so conftest's fence stands aside and the
    # probe controls the flag directly — which is what lets one test run both ways.
    if fenced:
        env["MOXIE_SKIP_DOTENV"] = "1"
    else:
        env.pop("MOXIE_SKIP_DOTENV", None)
    return subprocess.run(
        [sys.executable, str(runner), REPO,
         *(os.path.join("sim", "tests", f) for f in _UNDER_TEST)],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=900)


# --------------------------------------------------------------- the fence itself --
def test_the_fence_is_in_force_for_this_very_session():
    """The cheap one, and the one that notices the fence being deleted.

    An ordinary `pytest sim/tests` sets neither variable, so `conftest.py` sets
    `MOXIE_SKIP_DOTENV` before anything imports `config`. If that block is removed this
    fails immediately on a developer's machine *and* on CI — unlike the defect it guards,
    which was only ever visible on the machine that has the file.
    """
    if os.environ.get("MOXIE_DOTENV"):
        pytest.skip("this run names a dotenv explicitly, so the fence stood aside")
    assert os.environ.get("MOXIE_SKIP_DOTENV", "").strip().lower() \
        not in ("", "0", "off", "false", "no"), (
            "conftest.py no longer neutralises a deployment's mqtt/.env for the suite; "
            "a machine that has one will now disagree with CI about what is configured")


def test_an_explicit_opinion_still_wins():
    """The fence must not be a wall. Rule 20 was FOUND by running the suite against a real
    dotenv, so `MOXIE_SKIP_DOTENV=0` has to keep working as the way back in — otherwise
    the next defect of this shape has no door left to walk through."""
    import importlib
    sys.path.insert(0, os.path.join(REPO, "mqtt"))
    import config as _c
    prev = os.environ.get("MOXIE_SKIP_DOTENV")
    try:
        # Both directions, set explicitly rather than read off the ambient session, so
        # this stays a test about the switch instead of a second copy of the one above.
        os.environ["MOXIE_SKIP_DOTENV"] = "1"
        assert _c._truthy("MOXIE_SKIP_DOTENV") is True
        os.environ["MOXIE_SKIP_DOTENV"] = "0"
        assert _c._truthy("MOXIE_SKIP_DOTENV") is False
    finally:
        if prev is None:
            os.environ.pop("MOXIE_SKIP_DOTENV", None)
        else:
            os.environ["MOXIE_SKIP_DOTENV"] = prev
        importlib.reload(_c)


# ------------------------------------------------- the proof, in both directions --
def test_a_dotenv_cannot_perturb_the_suite_when_the_fence_is_up(tmp_path):
    """With the fence, a fully-populated dotenv sitting right where the loader looks
    changes nothing: the files that assert "nothing is configured" are all green."""
    r = _run_probe(tmp_path, fenced=True)
    assert r.returncode == 0, (
        "a dotenv perturbed a suite that claims nothing is configured:\n"
        + r.stdout[-4000:] + r.stderr[-2000:])


def test_and_WOULD_be_perturbed_without_it(tmp_path):
    """The mutation control — the half that makes the half above mean something.

    Without the fence the identical run goes red, which proves three things at once: the
    fixture is potent, the failures are caused by the dotenv rather than by the files
    being broken, and the fence in `conftest.py` is load-bearing rather than decorative.
    Measured 2026-09-05: 17 failed, 126 passed. The assertion is on the *shape* (some
    failures, in more than one file) and not on 17, because the number is a property of
    how many knobs happen to be documented today and would turn a passing change into a
    failing test for no reason.
    """
    r = _run_probe(tmp_path, fenced=False)
    assert r.returncode != 0, (
        "the fixture dotenv perturbed nothing, so the green test above proves nothing — "
        "either the loader stopped reading MOXIE_DOTENV or the fixture went stale:\n"
        + r.stdout[-4000:])
    failed = set(re.findall(r"^FAILED (sim/tests/[\w.]+)::", r.stdout, re.M))
    assert len(failed) >= 2, (
        f"expected the dotenv to reach several files, saw {sorted(failed)}")


# ------------------------------------------- the live tier must survive the fence --
def test_the_fence_does_not_reach_the_live_suites_credentials(tmp_path):
    """The property requirement (4) rests on, pinned so it cannot be optimised away.

    Every live suite finds its key through `helpers_runtime.load_repo_dotenv()`, which is
    a **separate** loader from `config._load_env` and deliberately does not consult
    `MOXIE_SKIP_DOTENV`. That is what lets the hermetic tier declare "nothing is
    configured" while the live tier still runs with real credentials in the same session.
    If someone ever routes the helper through the config loader "for consistency", every
    live suite would start skipping silently on a machine that has credentials — a green
    run that tested nothing, which is the exact regression PR #157 was opened to fix.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from helpers_runtime import load_repo_dotenv
    probe, value = "MOXIE_WEBHOOK_ENDPOINT", "http://127.0.0.1:9/from-the-fixture"
    f = tmp_path / "creds.env"
    f.write_text(f"{probe}={value}\n")
    prev = os.environ.pop(probe, None)
    try:
        os.environ["MOXIE_SKIP_DOTENV"] = "1"        # the fence, at its strongest
        assert load_repo_dotenv(str(f)) == str(f)
        assert os.environ.get(probe) == value, (
            "load_repo_dotenv now honours MOXIE_SKIP_DOTENV, so the live tier can no "
            "longer find credentials while the hermetic tier is insulated")
    finally:
        os.environ.pop(probe, None)
        if prev is not None:
            os.environ[probe] = prev


def test_the_fixture_covers_every_documented_knob():
    """The fixture is an ALLOWLIST, checked. The helpers this guard replaces carried a
    denylist of names to delete and silently stopped covering whatever nobody added to it;
    here a new knob in `.env.example` fails this test until it is represented above, so
    the guard cannot quietly shrink while looking green."""
    with open(ENV_EXAMPLE) as fh:
        documented = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", fh.read(), re.M))
    assert documented, "read no knobs out of mqtt/.env.example"
    assert documented <= set(_FIXTURE_ENV), (
        "mqtt/.env.example documents knobs this guard's fixture does not set, so the "
        f"proof below is weaker than it looks: {sorted(documented - set(_FIXTURE_ENV))}")
