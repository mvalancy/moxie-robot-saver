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

**The second door, and the second half of this file.** Fencing `config._load_env` fenced
one of the two loaders. `helpers_runtime.load_repo_dotenv` is the other, and it must NOT
be fenced — it is how ten `test_live_*.py` modules find a real key at import, and closing
it would turn every one of them into a silent skip (PR #157's lesson). It was *narrowed* instead: it exports
only `helpers_runtime.LIVE_KEYS`, the credentials, endpoints and model names the live
modules actually read, and drops the rest of the file on the floor. Measured 2026-09-05
with the maximal fixture below at the default path, the un-narrowed loader turned **21
tests red** in six files, and — worse, because it is green — exported
`MOXIE_ALLOW_UNVERIFIED_BOTS=1` into thirteen `test_device_permits.py` tests whose whole
claim is that an unpermitted stranger is refused. The last section of this file is that
narrowing's guard: it re-derives the allowlist from the live suites by AST, pins it
against `mqtt/.env.example`, and runs the same probe with the allowlist widened back to
the whole file to show the narrowing is load-bearing rather than decorative.

**How it proves it, rather than asserting it.** A throwaway dotenv is written here, into
`tmp_path`, and the suite is run against it in a subprocess in both configurations. With
the fence the run is green; without it the same run is **red**, which is what stops this
from being a test that would pass against a fence made of nothing. The mutation control is
the point: half two ("with the flag, the suite is clean") passes trivially in a worktree,
where there is no dotenv to be clean of — which is precisely the blind spot that hid the
defect for a day, so the red half is the half that means something.

**It never goes near a developer's own `mqtt/.env`.** That file is somebody's real
configuration containing a real key; nothing here reads it, writes it, moves it or asks
what is in it. Loader one is pointed at this file's own fixture with `MOXIE_DOTENV`, the
seam `config.py` documents for exactly this purpose; loader two is handed that same
fixture's path directly.

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
    # A real credential name, not an invented one: since the loader was narrowed to
    # `LIVE_KEYS` the interesting property is that an *allowlisted* key still crosses
    # while the fence is at its strongest. A probe the allowlist drops anyway would pass
    # this test for the wrong reason and keep passing if the fence swallowed the tier.
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


# ======================================================================================
# LOADER TWO — `helpers_runtime.load_repo_dotenv`, narrowed to the live tier's needs
# ======================================================================================
# Everything above is about the loader the *appliance* uses. This half is about the one
# the *tests* use, which cannot be switched off for the reason the section above ends on,
# and so is limited in what it may export instead.

#: The live modules. Globbed rather than listed: a new one appears here the day it is
#: written, which is what makes the derivation below a derivation.
def _live_modules():
    import glob
    return sorted(glob.glob(os.path.join(REPO, "sim", "tests", "test_live_*.py")))


def _env_reads(src):
    """Every environment variable a module READS, by AST.

    Reads only — `os.environ.get("X")` and `os.environ["X"]` in a load context. A name the
    module *writes* (`os.environ["X"] = …`) or *deletes* (`.pop`) is excluded on purpose:
    a value a test sets for itself, or scrubs before reloading `config`, cannot need to
    arrive from a developer's file. That distinction is the whole reason
    `MOXIE_VOICE_FORMAT` is not in the allowlist even though three live modules name it.
    """
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
    """The allowlist is DERIVED, and this is the derivation — run, not remembered.

    Both directions matter and for different reasons. A live module reading a key nobody
    allowlisted is the PR #157 failure in slow motion: the suite would not skip loudly, it
    would run with a credential missing and fail or degrade. A key in the allowlist that no
    live module reads is a door held open for nothing — which is exactly how
    `MOXIE_ALLOW_UNVERIFIED_BOTS` got into a hermetic test's environment in the first
    place. So this is an equality, and either kind of drift is a red test.
    """
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


#: What the allowlist and `mqtt/.env.example` are allowed to have in common: the nine
#: documented knobs that name a credential, an endpoint or a model. Pinned as a literal so
#: that widening the allowlist to a tenth documented knob has to be typed here too, in a
#: diff a reviewer reads — the drift check `#169`'s fixture allowlist gets, pointed the
#: other way.
_DOCUMENTED_AND_ALLOWED = {
    "MOXIE_LLM_API_KEY", "MOXIE_LLM_BASE_URL", "MOXIE_LLM_MODEL",
    "MOXIE_VOICE_API_KEY", "MOXIE_VOICE_BASE_URL", "MOXIE_VOICE_MODEL",
    "MOXIE_STT_API_KEY", "MOXIE_STT_BASE_URL", "MOXIE_STT_MODEL",
}

#: The knobs whose export is the actual damage, named so the test says what it is
#: protecting rather than counting. `MOXIE_ALLOW_UNVERIFIED_BOTS` is first because it is
#: the one that fails GREEN: with it exported, thirteen `test_device_permits.py` tests
#: asserting "an unpermitted stranger is refused" passed while the gate stood open.
_MUST_NEVER_CROSS = ("MOXIE_ALLOW_UNVERIFIED_BOTS", "MOXIE_APP", "MOXIE_STT", "MOXIE_TTS",
                     "MOXIE_PIPER_MODEL", "MOXIE_STREAMING", "MOXIE_EXPRESSIVE")


def test_the_allowlist_cannot_drift_against_the_documented_knobs():
    """Pinned against `mqtt/.env.example`, in both directions.

    `.env.example` is the list of everything a developer is *told* to put in the file, so
    it is the right yardstick for "what could be sitting in there". Nine of its knobs are
    credentials/endpoints/models and cross; the other sixteen are appliance behaviour and
    must not. A new documented knob therefore lands outside the allowlist by default,
    which is the safe direction — and moving it inside means editing the literal above.
    """
    sys.path.insert(0, os.path.join(REPO, "sim", "tests"))
    from helpers_runtime import LIVE_KEYS
    with open(ENV_EXAMPLE) as fh:
        documented = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", fh.read(), re.M))
    assert documented, "read no knobs out of mqtt/.env.example"
    assert documented & set(LIVE_KEYS) == _DOCUMENTED_AND_ALLOWED, (
        "the set of documented knobs a deployment's mqtt/.env may export has changed; "
        "if that is intended, say so in _DOCUMENTED_AND_ALLOWED: "
        f"{sorted(documented & set(LIVE_KEYS) ^ _DOCUMENTED_AND_ALLOWED)}")
    for knob in _MUST_NEVER_CROSS:
        assert knob not in LIVE_KEYS, (
            f"{knob} is behaviour, not a credential — allowlisting it lets a developer's "
            "own configuration decide what a hermetic test asserts")


def test_no_live_suite_widens_the_allowlist_for_itself():
    """The `allow=` seam is for guards, and stays that way.

    `load_repo_dotenv(path, allow=…)` exists so this file can put the old behaviour back
    and show it red. It would also be a perfectly quiet way for one live suite to reopen
    the door for everybody, since the export is process-wide and permanent — so the seam
    is only usable where a reviewer can see it, and that is enforced rather than asked for.
    """
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


#: The allowlist probe. Loads the fixture through the REAL `load_repo_dotenv` before
#: running the files under test, exactly as a live module does at collection time, and
#: varies only the allowlist. `config._load_env` is fenced for both runs (`MOXIE_SKIP_DOTENV`)
#: so the one thing that differs between green and red is loader two's allowlist.
_ALLOW_PROBE = """
import os, sys
repo, fixture, mode = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, os.path.join(repo, "sim", "tests"))
import helpers_runtime as H
# "wide" is the loader as it was before the narrowing: every key in the file. Spelled as
# the file's own key names rather than a magic value, so there is no "export everything"
# switch in the shipped API for anyone to find later.
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
    """The mutation control for the allowlist — widen it back to the whole file and the
    identical run goes red, which is what makes the green one above mean something.

    Measured 2026-09-05 across these four files: **14 failed** wide, against 143 passed
    narrow. The assertion is on the shape (failures, in more than one file) rather than on
    14, for the reason its counterpart above gives: the number tracks how many knobs happen
    to be documented today. Whole-suite the same widening is 21 failures in six files,
    thirteen of them `test_device_permits.py` tests that had been passing with the gate
    open.
    """
    r = _run_allow_probe(tmp_path, narrow=False)
    assert r.returncode != 0, (
        "widening the allowlist back to the whole file perturbed nothing, so the green "
        "test above proves nothing — the fixture or the loader has gone stale:\n"
        + r.stdout[-4000:])
    failed = set(re.findall(r"^FAILED (sim/tests/[\w.]+)::", r.stdout, re.M))
    assert len(failed) >= 2, (
        f"expected the dotenv to reach several files, saw {sorted(failed)}")


# ------------------------------- and the half the allowlist cannot reach on its own --
# The allowlist stops sixteen of the twenty-one. The other five move on the credentials
# themselves, because `MOXIE_STT=auto` means "gateway when a URL and a key are present" —
# so an endpoint the live tier cannot do without is, to a hermetic test, a configured
# gateway. `conftest.hermetic_tier_sees_no_credentials` is the answer: the keys stay in
# `os.environ` for collection, where live modules read them into their constants, and are
# hidden for the duration of every test outside a `test_live_*.py` file.

#: One `LIVE_KEYS` name, exported into the probe's real environment (no dotenv involved,
#: so this proves the same thing on CI as on a developer's box). `MOXIE_VOICE_MODEL`
#: because it is the one that moved three `test_voice_settings.py` defaults.
_SCRUB_PROBE_KEY, _SCRUB_PROBE_VALUE = "MOXIE_VOICE_MODEL", "fixture-tts"

#: Two one-assertion files that say what each tier is allowed to see. Written into a
#: throwaway directory next to a COPY of `conftest.py`, so pytest applies the real fixture
#: to a pair of tests whose only content is the property under test.
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
    """Both tiers, one pytest run, against a copy of the real `conftest.py`.

    `fenced=False` removes the `autouse` decorator from the fixture and changes nothing
    else — the smallest mutation that turns the fence off, and one that also fails loudly
    if the fixture is ever renamed away.
    """
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
    """Both halves at once: hermetic sees nothing, live still sees everything.

    The second half is the one that keeps this honest. A fence that simply deleted the
    credentials would pass the first assertion and quietly reintroduce PR #157 — every
    live suite skipping on a machine that has a key — so the fixture is only correct if
    the same run proves a `test_live_*.py` file still gets its value.
    """
    r = _run_scrub_probe(tmp_path, fenced=True)
    assert r.returncode == 0, (
        "either a hermetic test saw a credential, or a live one stopped seeing it:\n"
        + r.stdout[-3000:] + r.stderr[-1000:])


def test_and_the_hermetic_tier_WOULD_see_them_without_it(tmp_path):
    """The mutation control: drop the `autouse` and only the hermetic probe goes red.

    Measured 2026-09-05: 1 failed, 1 passed — and it is *which* one fails that matters,
    so the file name is asserted rather than the count. If both had failed the fixture
    would be doing something other than what its docstring claims.
    """
    r = _run_scrub_probe(tmp_path, fenced=False)
    assert r.returncode != 0, (
        "removing the fixture changed nothing, so the test above proves nothing:\n"
        + r.stdout[-3000:])
    assert "test_hermetic_probe.py" in r.stdout and "test_live_probe.py" not in r.stdout, (
        "the wrong tier moved when the fence came off — the fixture is not doing what it "
        f"says:\n{r.stdout[-3000:]}")


def test_no_credential_is_visible_to_this_very_test():
    """The cheap one, in the real session: whatever this developer has configured, a
    hermetic test is not reading it. Trivially true on CI and load-bearing on the one
    machine that has an `mqtt/.env` — which is the machine the whole class of defect
    was only ever visible on."""
    sys.path.insert(0, os.path.join(REPO, "sim", "tests"))
    from helpers_runtime import LIVE_KEYS
    leaked = sorted(k for k in LIVE_KEYS if k in os.environ)
    assert not leaked, (
        f"the live tier's credentials are visible to a hermetic test: {leaked} — this run "
        "is not the run CI does, and the disagreement will be blamed on CI")
