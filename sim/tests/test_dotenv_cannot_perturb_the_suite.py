"""
The dotenv fence (playbook rule 20), and the proof that it is load-bearing.

`mqtt/.env` is git-ignored: it exists on a developer's checkout and on no CI runner or
worktree. Two loaders can leak it into the suite:

1. `config._load_env` copies every key into `os.environ` with `setdefault` on the FIRST
   `import config` of the session. A per-helper `MOXIE_SKIP_DOTENV=1` arrives after that
   race is lost, and a denylist of names never covers the next knob — so tests claiming
   "nothing is configured" passed alone and failed in the full suite. `conftest.py`
   therefore fences it once, before the first import.
2. `helpers_runtime.load_repo_dotenv` must NOT be fenced — it is how the `test_live_*.py`
   suites find a real key at import, and fencing it turns them into silent skips. It is
   narrowed to `LIVE_KEYS` instead, and `conftest.hermetic_tier_sees_no_credentials` hides
   even those from hermetic tests while they run.

Each half is proved in both directions: a subprocess runs real suites against a throwaway
maximal dotenv with the guard on (green) and off (RED — the mutation control that stops
this passing against a fence made of nothing). Nothing here reads or touches a
developer's own `mqtt/.env`; credentials are blanked so no probe can reach a gateway.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ENV_EXAMPLE = os.path.join(REPO, "mqtt", ".env.example")

#: Files with "nothing is configured" tests, ~2 s together so the guard stays cheap.
_UNDER_TEST = ("test_assemble.py", "test_voice_settings.py",
               "test_device_permits.py", "test_stt_gateway.py")

#: A plausible developer's dotenv: every knob `mqtt/.env.example` documents, set to a VALID
#: value so the probe fails the way a real machine does (`.invalid` never resolves).
#: `test_the_fixture_covers_every_documented_knob` keeps it in step with `.env.example` —
#: an allowlist that fails on a new knob, where the old denylist silently missed it.
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

#: Runs the files under test having imported `config` FIRST — the defect being reproduced,
#: made deterministic instead of dependent on collection order.
_PROBE = """
import os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "mqtt"))
import config                      # the first import of the session — the polluting one
import pytest
sys.exit(int(pytest.main(["-q", "-p", "no:cacheprovider", *sys.argv[2:]])))
"""

#: Blanked (not deleted — `setdefault` leaves an empty value alone) for every subprocess.
_CREDENTIALS = ("MOXIE_LLM_API_KEY", "LITELLM_MASTER_KEY",
                "MOXIE_VOICE_API_KEY", "MOXIE_STT_API_KEY")


def _documented_knobs() -> set:
    with open(ENV_EXAMPLE) as fh:
        documented = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", fh.read(), re.M))
    assert documented, "read no knobs out of mqtt/.env.example"
    return documented


def _write_fixture(tmp_path):
    f = tmp_path / "fixture.env"
    f.write_text("# throwaway fixture written by a test — never a real deployment\n"
                 + "".join(f"{k}={v}\n" for k, v in _FIXTURE_ENV.items()))
    return f


def _run_probe(tmp_path, *, fenced):
    """The files under test against the fixture: `fenced=True` is what `conftest.py`
    arranges for every run; `fenced=False` is the old state and must come back red."""
    runner = tmp_path / "run_probe.py"
    runner.write_text(_PROBE)
    # No inherited `MOXIE_*`: the fixture file must be the only thing the run reacts to.
    env = {k: v for k, v in os.environ.items() if not k.startswith("MOXIE_")}
    env.update(MOXIE_DOTENV=str(_write_fixture(tmp_path)),
               **{k: "" for k in _CREDENTIALS})
    # Naming MOXIE_DOTENV makes conftest stand aside, so the probe owns the flag.
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
    """`conftest.py` sets `MOXIE_SKIP_DOTENV` for an ordinary run — so deleting that block
    fails here on every machine, not only the one that has the file."""
    if os.environ.get("MOXIE_DOTENV"):
        pytest.skip("this run names a dotenv explicitly, so the fence stood aside")
    assert os.environ.get("MOXIE_SKIP_DOTENV", "").strip().lower() \
        not in ("", "0", "off", "false", "no"), (
            "conftest.py no longer neutralises a deployment's mqtt/.env for the suite; "
            "a machine that has one will now disagree with CI about what is configured")


def test_an_explicit_opinion_still_wins():
    """`MOXIE_SKIP_DOTENV=0` must keep working as the way back in: rule 20 was FOUND by
    running the suite against a real dotenv."""
    import importlib
    sys.path.insert(0, os.path.join(REPO, "mqtt"))
    import config as _c
    prev = os.environ.get("MOXIE_SKIP_DOTENV")
    try:
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
    """The mutation control: without the fence the identical run goes red, proving the
    fixture is potent and the fence load-bearing. Asserted on the shape (failures in
    several files), not a count that tracks how many knobs are documented today."""
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
    """`load_repo_dotenv` is a SEPARATE loader that deliberately ignores
    `MOXIE_SKIP_DOTENV`. Routing it through the config loader "for consistency" would make
    every live suite skip silently on a machine that has credentials."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from helpers_runtime import load_repo_dotenv
    # an ALLOWLISTED key, so this cannot pass merely because the allowlist dropped it
    probe, value = "MOXIE_STT_API_KEY", "fixture-not-a-real-key"
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
    """A new knob in `.env.example` fails this until the fixture sets it, so the guard
    cannot quietly shrink while looking green."""
    documented = _documented_knobs()
    assert documented <= set(_FIXTURE_ENV), (
        "mqtt/.env.example documents knobs this guard's fixture does not set, so the "
        f"proof below is weaker than it looks: {sorted(documented - set(_FIXTURE_ENV))}")


# ======================================================================================
# LOADER TWO — `helpers_runtime.load_repo_dotenv`, narrowed to the live tier's needs
# ======================================================================================

def _live_modules():
    """Globbed, so a new live module joins the derivation below the day it is written."""
    import glob
    return sorted(glob.glob(os.path.join(REPO, "sim", "tests", "test_live_*.py")))


def _env_reads(src):
    """Every environment variable a module READS (`environ.get("X")` / `environ["X"]` in
    a load context), by AST. Writes and pops are excluded: a value a test sets or scrubs
    for itself cannot need to arrive from a developer's file (hence no
    `MOXIE_VOICE_FORMAT` in the allowlist)."""
    import ast
    reads = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
           and node.value.attr == "environ" and isinstance(node.ctx, ast.Load) \
           and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            reads.add(node.slice.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
           and node.func.attr == "get" and isinstance(node.func.value, ast.Attribute) \
           and node.func.value.attr == "environ" and node.args \
           and isinstance(node.args[0], ast.Constant) \
           and isinstance(node.args[0].value, str):
            reads.add(node.args[0].value)
    return reads


def test_the_allowlist_is_exactly_what_the_live_suites_read():
    """An equality, both ways: a key read but not allowlisted makes a live suite run
    degraded; a key allowlisted but read by nobody is a door held open for nothing
    (how `MOXIE_ALLOW_UNVERIFIED_BOTS` once reached hermetic tests)."""
    sys.path.insert(0, os.path.join(REPO, "sim", "tests"))
    from helpers_runtime import LIVE_KEYS
    modules = _live_modules()
    assert len(modules) >= 8, f"found only {len(modules)} live modules — glob went wrong"
    derived = set()
    for m in modules:
        derived |= _env_reads(open(m).read())
    assert derived == set(LIVE_KEYS), (
        "helpers_runtime.LIVE_KEYS no longer matches what the live suites read.\n"
        f"  read but NOT allowed (these suites will run without them): "
        f"{sorted(derived - set(LIVE_KEYS))}\n"
        f"  allowed but read by NO live suite (an open door for nothing): "
        f"{sorted(set(LIVE_KEYS) - derived)}")


#: The documented knobs allowed to cross (credential / endpoint / model). A literal, so
#: widening the allowlist to another documented knob is a diff a reviewer reads.
_DOCUMENTED_AND_ALLOWED = {
    "MOXIE_LLM_API_KEY", "MOXIE_LLM_BASE_URL", "MOXIE_LLM_MODEL",
    "MOXIE_VOICE_API_KEY", "MOXIE_VOICE_BASE_URL", "MOXIE_VOICE_MODEL",
    "MOXIE_STT_API_KEY", "MOXIE_STT_BASE_URL", "MOXIE_STT_MODEL",
}

#: Behaviour knobs whose export is the actual damage. `MOXIE_ALLOW_UNVERIFIED_BOTS` fails
#: GREEN: exported, "an unpermitted stranger is refused" tests passed with the gate open.
_MUST_NEVER_CROSS = ("MOXIE_ALLOW_UNVERIFIED_BOTS", "MOXIE_APP", "MOXIE_STT", "MOXIE_TTS",
                     "MOXIE_PIPER_MODEL", "MOXIE_STREAMING", "MOXIE_EXPRESSIVE")


def test_the_allowlist_cannot_drift_against_the_documented_knobs():
    """`.env.example` is what a developer is told to put in the file; a new documented
    knob lands outside the allowlist by default, the safe direction."""
    sys.path.insert(0, os.path.join(REPO, "sim", "tests"))
    from helpers_runtime import LIVE_KEYS
    documented = _documented_knobs()
    assert documented & set(LIVE_KEYS) == _DOCUMENTED_AND_ALLOWED, (
        "the set of documented knobs a deployment's mqtt/.env may export has changed; "
        "if that is intended, say so in _DOCUMENTED_AND_ALLOWED: "
        f"{sorted(documented & set(LIVE_KEYS) ^ _DOCUMENTED_AND_ALLOWED)}")
    for knob in _MUST_NEVER_CROSS:
        assert knob not in LIVE_KEYS, (
            f"{knob} is behaviour, not a credential — allowlisting it lets a developer's "
            "own configuration decide what a hermetic test asserts")


def test_no_live_suite_widens_the_allowlist_for_itself():
    """`allow=` exists so a guard can show the old behaviour red; the export is
    process-wide, so a live suite using it would quietly reopen the door for everyone."""
    for m in _live_modules():
        src = open(m).read()
        assert "allow=" not in src, (
            f"{os.path.basename(m)} passes allow= to the dotenv loader; a live suite that "
            "needs another key should add it to LIVE_KEYS, where the derivation test and "
            "the .env.example pin can both see it")


def test_a_deployments_dotenv_cannot_export_a_behavioural_knob(tmp_path):
    """The narrowing itself, at the seam, with no subprocess: an allowlisted key crosses
    and a behavioural one does not, from the same file in the same call."""
    sys.path.insert(0, os.path.join(REPO, "sim", "tests"))
    from helpers_runtime import load_repo_dotenv
    allowed, denied = "MOXIE_DEMO_ORIGIN", "MOXIE_ALLOW_UNVERIFIED_BOTS"
    f = tmp_path / "maximal.env"
    f.write_text(f"{allowed}=http://127.0.0.1:9/from-the-fixture\n{denied}=1\n"
                 "MOXIE_APP=echo\nMOXIE_PIPER_MODEL=/fixture/voice.onnx\n")
    keep = {k: os.environ.get(k) for k in (allowed, denied, "MOXIE_APP",
                                           "MOXIE_PIPER_MODEL")}
    for k in keep:
        os.environ.pop(k, None)
    try:
        assert load_repo_dotenv(str(f)) == str(f)
        assert os.environ.get(allowed) == "http://127.0.0.1:9/from-the-fixture", (
            "an allowlisted endpoint did not reach the environment — the live tier just "
            "lost its configuration")
        for k in (denied, "MOXIE_APP", "MOXIE_PIPER_MODEL"):
            assert k not in os.environ, (
                f"{k} came out of a dotenv and is now set for the rest of the session")
    finally:
        for k, v in keep.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


#: Loads the fixture through the REAL `load_repo_dotenv` (as a live module does at
#: collection) and varies only the allowlist; `config._load_env` is fenced in both runs.
_ALLOW_PROBE = """
import os, sys
repo, fixture, mode = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, os.path.join(repo, "sim", "tests"))
import helpers_runtime as H
# "wide" = every key in the file, spelled as its names so the API has no export-all switch
allow = H.LIVE_KEYS if mode == "narrow" else tuple(H.dotenv_values(fixture))
H.load_repo_dotenv(fixture, allow=allow)
import pytest
sys.exit(int(pytest.main(["-q", "-p", "no:cacheprovider", *sys.argv[4:]])))
"""


def _run_allow_probe(tmp_path, *, narrow):
    runner = tmp_path / "run_allow_probe.py"
    runner.write_text(_ALLOW_PROBE)
    env = {k: v for k, v in os.environ.items() if not k.startswith("MOXIE_")}
    env.update(MOXIE_SKIP_DOTENV="1", **{k: "" for k in _CREDENTIALS})
    return subprocess.run(
        [sys.executable, str(runner), REPO, str(_write_fixture(tmp_path)),
         "narrow" if narrow else "wide",
         *(os.path.join("sim", "tests", f) for f in _UNDER_TEST)],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=900)


def test_a_deployments_dotenv_cannot_perturb_the_suite_through_the_live_tiers_loader(tmp_path):
    """With the allowlist, the maximal fixture reaches the live tier's credentials and
    nothing else: the files that assert "nothing is configured" are all green."""
    r = _run_allow_probe(tmp_path, narrow=True)
    assert r.returncode == 0, (
        "a dotenv perturbed the suite through helpers_runtime.load_repo_dotenv:\n"
        + r.stdout[-4000:] + r.stderr[-2000:])


def test_and_WOULD_be_perturbed_by_the_un_narrowed_loader(tmp_path):
    """The mutation control: widen the allowlist back to the whole file and the same run
    goes red (asserted on shape, not count)."""
    r = _run_allow_probe(tmp_path, narrow=False)
    assert r.returncode != 0, (
        "widening the allowlist back to the whole file perturbed nothing, so the green "
        "test above proves nothing — the fixture or the loader has gone stale:\n"
        + r.stdout[-4000:])
    failed = set(re.findall(r"^FAILED (sim/tests/[\w.]+)::", r.stdout, re.M))
    assert len(failed) >= 2, (
        f"expected the dotenv to reach several files, saw {sorted(failed)}")


# ------------------------------- and the half the allowlist cannot reach on its own --
# Credentials themselves move tests (`MOXIE_STT=auto` means gateway when a URL and key are
# present), so `conftest.hermetic_tier_sees_no_credentials` hides `LIVE_KEYS` during every
# test outside a `test_live_*.py` file. Proved on a real env var, so it holds on CI too.

_SCRUB_PROBE_KEY, _SCRUB_PROBE_VALUE = "MOXIE_VOICE_MODEL", "fixture-tts"

#: One-assertion files run next to a COPY of `conftest.py`, so the real fixture applies.
_SCRUB_HERMETIC = f"""
import os
def test_a_hermetic_test_cannot_see_the_live_tiers_credentials():
    assert {_SCRUB_PROBE_KEY!r} not in os.environ
"""
_SCRUB_LIVE = f"""
import os
def test_a_live_suite_still_can():
    assert os.environ.get({_SCRUB_PROBE_KEY!r}) == {_SCRUB_PROBE_VALUE!r}
"""


def _run_scrub_probe(tmp_path, *, fenced):
    """`fenced=False` only drops the `autouse` decorator — the smallest mutation that
    turns the fence off, which also fails loudly if the fixture is renamed."""
    d = tmp_path / ("fenced" if fenced else "unfenced")
    d.mkdir()
    src = open(os.path.join(REPO, "sim", "tests", "conftest.py")).read()
    marker = "@pytest.fixture(autouse=True)\ndef hermetic_tier_sees_no_credentials"
    assert marker in src, (
        "conftest.py no longer defines an autouse `hermetic_tier_sees_no_credentials`; "
        "if it was renamed, rename it here too — if it was deleted, a developer's gateway "
        "is once again deciding what the hermetic tier asserts")
    (d / "conftest.py").write_text(
        src if fenced else src.replace(marker, "def hermetic_tier_sees_no_credentials"))
    (d / "test_hermetic_probe.py").write_text(_SCRUB_HERMETIC)
    (d / "test_live_probe.py").write_text(_SCRUB_LIVE)
    env = {k: v for k, v in os.environ.items() if not k.startswith("MOXIE_")}
    env.update({_SCRUB_PROBE_KEY: _SCRUB_PROBE_VALUE,
                # the copied conftest imports `helpers_runtime` for LIVE_KEYS
                "PYTHONPATH": os.path.join(REPO, "sim", "tests")})
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           str(d)], cwd=str(d), env=env, capture_output=True, text=True,
                          timeout=300)


def test_the_hermetic_tier_is_blind_to_the_live_tiers_credentials(tmp_path):
    """Hermetic sees nothing AND live still sees its value — a fence that just deleted
    the credentials would pass the first half and silently skip every live suite."""
    r = _run_scrub_probe(tmp_path, fenced=True)
    assert r.returncode == 0, (
        "either a hermetic test saw a credential, or a live one stopped seeing it:\n"
        + r.stdout[-3000:] + r.stderr[-1000:])


def test_and_the_hermetic_tier_WOULD_see_them_without_it(tmp_path):
    """Drop the `autouse` and ONLY the hermetic probe goes red — which one fails is what
    matters, so the file name is asserted rather than a count."""
    r = _run_scrub_probe(tmp_path, fenced=False)
    assert r.returncode != 0, (
        "removing the fixture changed nothing, so the test above proves nothing:\n"
        + r.stdout[-3000:])
    assert "test_hermetic_probe.py" in r.stdout and "test_live_probe.py" not in r.stdout, (
        "the wrong tier moved when the fence came off — the fixture is not doing what it "
        f"says:\n{r.stdout[-3000:]}")


def test_no_credential_is_visible_to_this_very_test():
    """In the real session: load-bearing on the one machine that has an `mqtt/.env`."""
    sys.path.insert(0, os.path.join(REPO, "sim", "tests"))
    from helpers_runtime import LIVE_KEYS
    leaked = sorted(k for k in LIVE_KEYS if k in os.environ)
    assert not leaked, (
        f"the live tier's credentials are visible to a hermetic test: {leaked} — this run "
        "is not the run CI does, and the disagreement will be blamed on CI")
