"""
The CI harness, guarded as code — because a red push that a PR check never saw is not
a CI bug, it is an *unproven assumption about CI* that nothing in the repo asserted.

Post-mortem this file exists for (2026-09-02, integration pass after PRs #43–#47):
`test_telehealth.py::test_the_compiled_proto_agrees_with_the_recovered_text` reddened
every `dev` **push** run from PR #43 to PR #46, while the merge gate believed the
matching **pull_request** runs were green. They were not — every one of them failed on
the identical step with the identical `ModuleNotFoundError: No module named 'embodied'`
(PR-side run 33647023938 at 15:19:16Z; the dev-push run 33647274170 of the same content
at 15:19:54Z). The PRs had simply been merged ~2m23s after opening, while the `sil` job
needs 5½–7 minutes — so the only *finished* check at merge time was the 10-second `docs`
job, and "no conclusion yet" read as "not failing". `dev` has no branch protection and
no auto-merge was used, so nothing made the gate wait.

Two invariants come out of that, and this file is where they live:

* **Push and pull_request must execute the same thing** in the fast tier, so the two
  outcomes can never legitimately differ. They do today — `sim/ci/ci.yml` has no `if:`,
  no `paths:` filter and no `concurrency:` group — but nothing asserted it, which is why
  "the PR run ran something else" stayed a plausible story for hours.
* **The installed workflows must equal their templates.** `.github/workflows/*.yml` can
  only be pushed with a workflow-scoped token, so the repo version-controls the real
  source at `sim/ci/*.yml` and copies it across by hand. A hand copy is a drift waiting
  to happen, and drift here is invisible until CI behaves unlike the file we read.

Pure file/YAML reading — no network, no `gh`, no runner. Skips only where PyYAML is
absent (both venv shapes install it; the fast tier installs it too).
"""
from __future__ import annotations

import json
import os
import re
import sys

import pytest

yaml = pytest.importorskip("yaml", reason="the workflow guards parse YAML")

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TEMPLATES = os.path.join(REPO, "sim", "ci")
INSTALLED = os.path.join(REPO, ".github", "workflows")

#: The three tiers, by file name. `sil-and-cicd.md` documents them; the deep tier's
#: extra jobs and the release tier's tag trigger are deliberate, so only the fast tier
#: is held to the event-symmetry rule below.
TIERS = ("ci.yml", "ci-deep.yml", "release.yml")
FAST = "ci.yml"


def _load(path: str) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh)


def _triggers(doc: dict) -> dict:
    """The `on:` block. PyYAML reads bare `on` as the YAML 1.1 boolean `True`, so a
    workflow's trigger map is under the key `True` unless it was quoted."""
    return doc.get("on", doc.get(True)) or {}


def _steps(job: dict) -> list:
    return list(job.get("steps") or [])


@pytest.fixture(scope="module")
def fast() -> dict:
    return _load(os.path.join(TEMPLATES, FAST))


# --------------------------------------------------------------------------- #
# The templates and the installed workflows are the same bytes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", TIERS)
def test_the_installed_workflow_is_byte_identical_to_its_template(name):
    """`sim/ci/<tier>.yml` is the version-controlled source; `.github/workflows/<tier>.yml`
    is the copy GitHub actually runs. Nothing but a person keeps them equal."""
    tmpl = os.path.join(TEMPLATES, name)
    inst = os.path.join(INSTALLED, name)
    assert os.path.exists(tmpl), tmpl
    assert os.path.exists(inst), (
        f"{name} is templated at sim/ci/ but not installed under .github/workflows/")
    with open(tmpl, "rb") as a, open(inst, "rb") as b:
        left, right = a.read(), b.read()
    assert left == right, (
        f"{name} drifted: sim/ci/{name} and .github/workflows/{name} differ. "
        "Copy the template across (both must be committed in the same change).")


def test_every_installed_workflow_has_a_template():
    """The reverse direction: a workflow that exists only under `.github/` cannot be
    edited by a session without workflow scope, so it must not exist."""
    installed = {f for f in os.listdir(INSTALLED) if f.endswith((".yml", ".yaml"))}
    templated = {f for f in os.listdir(TEMPLATES) if f.endswith((".yml", ".yaml"))}
    assert installed <= templated, sorted(installed - templated)


# --------------------------------------------------------------------------- #
# The fast tier runs the SAME thing on a push and on a pull request
# --------------------------------------------------------------------------- #
def test_the_fast_tier_fires_on_push_and_pull_request_for_the_same_branches(fast):
    on = _triggers(fast)
    assert set(on) == {"push", "pull_request"}, sorted(on)
    assert on["push"]["branches"] == on["pull_request"]["branches"], on
    assert on["push"]["branches"] == ["dev"], on["push"]


def test_no_job_or_step_in_the_fast_tier_is_conditional_at_all(fast):
    """The post-mortem's headline invariant. One `if: github.event_name == 'push'`
    anywhere below would make a green PR check and a red push *legitimate*, and the
    merge gate would have no way to tell that from a race."""
    offenders = []
    for job_id, job in fast["jobs"].items():
        if "if" in job:
            offenders.append(f"job {job_id}: if: {job['if']}")
        for i, step in enumerate(_steps(job), 1):
            if "if" in step:
                offenders.append(
                    f"job {job_id} step {i} ({step.get('name', step.get('uses', '?'))}): "
                    f"if: {step['if']}")
    assert not offenders, (
        "the fast tier must execute identically for a push and for a pull request:\n  "
        + "\n  ".join(offenders))


def test_the_fast_tier_has_no_path_filter_and_no_cancelling_concurrency(fast):
    """A `paths:` filter under one event and not the other, or a `concurrency` group that
    cancels one run and not the other, are the other two ways the same commit can produce
    two different verdicts."""
    on = _triggers(fast)
    for event, spec in on.items():
        assert isinstance(spec, dict), (event, spec)
        assert not (set(spec) - {"branches"}), (
            f"{event} carries a filter beyond `branches`: {sorted(set(spec) - {'branches'})}")
    assert "concurrency" not in fast, fast.get("concurrency")
    for job_id, job in fast["jobs"].items():
        assert "concurrency" not in job, (job_id, job.get("concurrency"))


def test_the_fast_tier_runs_the_whole_pytest_suite(fast):
    """Playbook rule 9: the fast tier runs the WHOLE `sim/tests` suite with the fast
    tier's deps — a `-k`/`--ignore` here would hide exactly the kind of failure this
    file was written about."""
    runs = [s.get("run", "") for job in fast["jobs"].values() for s in _steps(job)]
    pytest_runs = [r for r in runs if "pytest sim/tests" in r]
    assert pytest_runs, "the fast tier no longer runs the pytest suite at all"
    whole = [r for r in pytest_runs if " -k " not in r and "--ignore" not in r]
    assert whole, ("every fast-tier pytest invocation now filters the suite:\n"
                   + "\n---\n".join(pytest_runs))


def test_the_fast_tier_fails_before_a_two_minute_merge_gate_can_open(fast):
    """The *timing* half of the post-mortem, as an assertion.

    Four PRs were merged 2m22s–2m28s after opening; the pytest step that would have
    failed them sat behind a browser install, ~4½ minutes in. So the fast tier now runs
    the hermetic suite EARLY, before anything that downloads a browser — a red suite
    reports inside the window a human (or a script) actually waits.
    """
    sil = fast["jobs"]["sil"]
    steps = _steps(sil)
    def _first(pred):
        return next((i for i, s in enumerate(steps) if pred(s)), None)

    early = _first(lambda s: "pytest sim/tests" in (s.get("run") or ""))
    browser = _first(lambda s: "playwright install" in (s.get("run") or ""))
    assert early is not None, "the sil job runs no pytest"
    assert browser is not None, "the sil job no longer installs a browser (update this guard)"
    assert early < browser, (
        f"the first pytest step (#{early + 1}) runs after the browser install "
        f"(#{browser + 1}); a hermetic failure would take minutes to surface")


def test_the_early_hermetic_step_installs_protobuf(fast):
    """Specifically: the failure that started this had `protobuf` present (installed for
    the QR-parity step) and no compiled protos. An early hermetic step without protobuf
    would `importorskip` past the very test that was red."""
    sil = fast["jobs"]["sil"]
    early = next(s for s in _steps(sil) if "pytest sim/tests" in (s.get("run") or ""))
    assert "protobuf" in early["run"], (
        "the early hermetic step must install protobuf, or the pb2 oracle silently skips:\n"
        + early["run"])


# --------------------------------------------------------------------------- #
# The headless node tests are actually WIRED — a test CI never runs is not a test
# --------------------------------------------------------------------------- #
#: The node tests that guard what the hosted static site TELLS A VISITOR, and what its
#: Pages Functions SPEND on a visitor's behalf.
#:
#: `test_mode.mjs` is the mode machine and the honesty guard behind the badge (spec
#: docs/architecture/backlog/live-sim-demo.md §6.3/§7); `test_env_hosted.mjs` is the
#: rendered page in every one of those modes. The six live-turn tests are the guards on
#: the THREE routes that can spend money: `test_demo_proxy.mjs` (the caps, the origin pin,
#: and the sweep proving the gateway key and base URL never appear in ANY response, on any
#: path), `test_demo_tickets.mjs` (forgery, expiry, replay, tampering, the constant-time
#: compare), `test_wav_decode.mjs` (both halves of the audio contract, sample for sample),
#: `test_demo_ears.mjs` (`/api/transcribe`'s byte caps — including the floor below which
#: NO upstream call is made at all — the per-IP windows, the timeout, an unset
#: `DEMO_STT_MODEL` spending nothing, and the 15-second recording cap proven to stop a
#: FAKE recorder, because the byte cap is not a duration cap for a compressed container),
#: `test_cloud_transport.mjs` (the voice-first ordering, on an injected clock) and
#: `test_fallback_coverage.mjs` (a degraded page has a real voice for the lines it plays).
#:
#: Every one is hermetic — the Functions are imported as ES modules with a plain object as
#: `context.env` and a stubbed `fetch`; no Cloudflare account and NO GATEWAY KEY is needed
#: by any of them, and none may ever be. `test_demo_ears.mjs` drives `sim/web/mic.js` with
#: an injected clock and a fake recorder, so it opens NO MICROPHONE either.
#: `test_env_hosted.mjs` skips cleanly with no browser. So there is no excuse for any of
#: them to be missing from the fast tier: an unwired guard on a money-spending route is
#: not a guard.
STATIC_SITE_NODE_TESTS = (
    "sim/test_mode.mjs",
    "sim/test_env_hosted.mjs",
    "sim/test_demo_proxy.mjs",
    "sim/test_demo_tickets.mjs",
    "sim/test_wav_decode.mjs",
    "sim/test_demo_ears.mjs",
    "sim/test_cloud_transport.mjs",
    "sim/test_fallback_coverage.mjs",
)

#: The subset that must report BEFORE anything downloads a browser — same reasoning as the
#: early-pytest guard: a red guard on a route that can spend money has to surface inside
#: the window a merge gate (or a script) actually waits.
EARLY_NODE_TESTS = (
    "sim/test_mode.mjs",
    "sim/test_demo_proxy.mjs",
    "sim/test_demo_tickets.mjs",
    "sim/test_wav_decode.mjs",
    "sim/test_demo_ears.mjs",
    "sim/test_cloud_transport.mjs",
)


def _node_steps(job: dict) -> list:
    """(index, script) for every `node sim/<file>.mjs` invocation in the job."""
    out = []
    for i, step in enumerate(_steps(job)):
        for token in (step.get("run") or "").split():
            if token.startswith("sim/test_") and token.endswith(".mjs"):
                out.append((i, token))
    return out


def _tier_node_steps(tier: dict) -> list:
    """(job_id, index, script) across EVERY job of the tier.

    The suites used to live in one job, so `_node_steps(fast["jobs"]["sil"])` was the
    whole tier. It is not any more: the eleven Chrome-launching suites moved to a
    parallel `browser` job, because eleven browsers on top of ~5,000 broker-backed pytest
    tests took `sil` from ~7–8 min to ~17 min and started reddening unrelated tests
    through load contention. A guard that kept reading only `sil` would have quietly
    stopped covering them — the same failure it exists to prevent.
    """
    out = []
    for job_id, job in tier["jobs"].items():
        for i, script in _node_steps(job):
            out.append((job_id, i, script))
    return out


@pytest.mark.parametrize("script", STATIC_SITE_NODE_TESTS)
def test_the_fast_tier_runs_the_static_site_honesty_tests(fast, script):
    wired = {s: j for j, _, s in _tier_node_steps(fast)}
    assert script in wired, (
        f"{script} is not run by ANY job of the fast tier; the honest-indicator contract "
        f"would be unproven on every push. Wired scripts: {sorted(wired)}")


def test_every_node_test_the_fast_tier_names_actually_exists(fast):
    """A rename or a typo in a `run:` line fails the job with "Cannot find module", which
    reads as a broken runner rather than as a broken workflow. Cheap to assert here.

    It also forbids PRE-WIRING a suite that does not exist yet — tempting when a sibling
    branch is about to add one, and a guaranteed red `dev` if that branch does not land.
    """
    missing = sorted({s for _, _, s in _tier_node_steps(fast)
                      if not os.path.exists(os.path.join(REPO, s))})
    assert not missing, missing


@pytest.mark.parametrize("script", EARLY_NODE_TESTS)
def test_the_hermetic_edge_tests_report_before_a_two_minute_merge_gate_can_open(fast, script):
    """Same reasoning as the early-pytest guard above, for the same window.

    Each of these is hermetic and takes about a second; running one behind the ~3-minute
    browser install would mean a red mode machine — or a leaked gateway key — surfaces
    minutes after a script could have merged it.
    """
    steps = _steps(fast["jobs"]["sil"])
    at = next((i for i, s in enumerate(steps) if script in (s.get("run") or "")), None)
    browser = next((i for i, s in enumerate(steps)
                    if "playwright install" in (s.get("run") or "")), None)
    assert at is not None, f"the fast tier no longer runs {script}"
    assert browser is not None, "the sil job no longer installs a browser (update this guard)"
    assert at < browser, (
        f"{script} (step #{at + 1}) runs after the browser install "
        f"(#{browser + 1}); a hermetic failure would take minutes to surface")


def test_no_hermetic_edge_test_needs_a_gateway_key_or_a_cloudflare_account(fast):
    """The rule that keeps the fast tier runnable on a fork, and keeps a secret out of CI.

    Every live-turn test imports `functions/api/*.js` and drives it with a synthetic
    `Request` and a plain object as `context.env`, with `fetch` stubbed — so none of them
    may reference a real credential or a deploy secret. A step that suddenly needed one
    would silently skip on a fork (or, worse, put a key in a workflow file).
    """
    steps = [s for job in fast["jobs"].values() for s in _steps(job)]
    for script in STATIC_SITE_NODE_TESTS:
        step = next((s for s in steps if script in (s.get("run") or "")), None)
        assert step is not None, f"{script} is not wired into the fast tier"
        run = step.get("run") or ""
        for forbidden in ("MOXIE_LLM_API_KEY", "DEMO_GATEWAY_API_KEY", "CLOUDFLARE_API_TOKEN",
                          "secrets.", "${{"):
            assert forbidden not in run, (
                f"the step running {script} references {forbidden!r}; these tests are "
                f"hermetic and must never need a credential:\n{run}")


# --------------------------------------------------------------------------- #
# The deep tier's event conditionals are the documented exception
# --------------------------------------------------------------------------- #
def test_the_only_event_conditionals_in_the_deep_tier_are_the_dispatch_only_live_tiers():
    """The deep tier legitimately gates the *live* stages on `workflow_dispatch` (they
    spend gateway calls and are fork-unsafe). Every such `if:` must say so, so that a new
    conditional is a deliberate act rather than an accident."""
    deep = _load(os.path.join(TEMPLATES, "ci-deep.yml"))
    for job_id, job in deep["jobs"].items():
        for i, step in enumerate(_steps(job), 1):
            cond = step.get("if")
            if not cond or "github.event" not in cond:
                continue
            assert "workflow_dispatch" in cond, (
                f"deep tier job {job_id} step {i} branches on the event without being "
                f"dispatch-only: {cond}")


# --------------------------------------------------------------------------- #
# Tier dependency parity — a test that CI never RUNS is not a test
# --------------------------------------------------------------------------- #
#: Everything the hermetic suite needs in order to actually execute, rather than to
#: `importorskip` past itself. Found the hard way on 2026-09-02: neither tier installed
#: fastapi/httpx, so all 55 `test_console_roundtrip.py` tests — the acceptance tests for
#: DoD criterion 3, the parent console's config/telemetry/safety/memory round trips —
#: skipped in CI on every run since they were written. `pyyaml` was likewise missing
#: from the deep tier, which silently skipped the compose parity guards (PR #34).
#:
#: `-r server/requirements.txt` rather than a hand-copied `fastapi pynacl …` list: the
#: console's deps are declared once, in the console's own file, so this cannot drift.
HERMETIC_TEST_DEPS = (
    "pytest",
    "openai",                      # LLMApp's injected-client seam still imports it
    "jinja2",                      # content-module templates
    "pyyaml",                      # compose parity guards + this file
    "httpx",                       # fastapi's TestClient
    "-r server/requirements.txt",  # fastapi + pynacl: the console app itself
)

#: Which file runs the hermetic suite, and which job in it.
HERMETIC_JOBS = ((FAST, "sil"), ("ci-deep.yml", "hil-sim"))


def _runs_up_to_pytest(job: dict) -> str:
    """Every `run:` block in the job up to and including the first hermetic pytest —
    which is where its deps must have been installed."""
    seen = []
    for step in _steps(job):
        run = step.get("run") or ""
        seen.append(run)
        if "pytest sim/tests" in run and " -k " in run:
            return "\n".join(seen)
    return "\n".join(seen)


@pytest.mark.parametrize("workflow,job_id", HERMETIC_JOBS)
@pytest.mark.parametrize("dep", HERMETIC_TEST_DEPS)
def test_both_tiers_install_the_same_hermetic_test_deps(workflow, job_id, dep):
    """Playbook rule 9, as an assertion: the tiers' hermetic test deps stay in parity.
    A dep only one tier installs is a tier drift that shows up as a red push rather than
    as a caught bug — or, worse, as a suite that quietly skips itself."""
    job = _load(os.path.join(TEMPLATES, workflow))["jobs"][job_id]
    text = _runs_up_to_pytest(job)
    assert dep in text, (
        f"{workflow} job `{job_id}` runs the hermetic suite without installing {dep!r}; "
        "the tests that need it will importorskip instead of running")


def test_the_console_round_trip_suite_can_actually_run_in_ci():
    """The concrete consequence, spelled out so nobody re-derives it: DoD criterion 3 is
    proven by `test_console_roundtrip.py`, and that file opens with
    `importorskip("fastapi")`. If CI does not install the console's own requirements, the
    whole file reports as one green skip."""
    gate = open(os.path.join(os.path.dirname(__file__),
                             "test_console_roundtrip.py")).read()
    assert 'importorskip("fastapi"' in gate, (
        "test_console_roundtrip.py no longer gates on fastapi — update this guard")
    for workflow, job_id in HERMETIC_JOBS:
        job = _load(os.path.join(TEMPLATES, workflow))["jobs"][job_id]
        assert "server/requirements.txt" in _runs_up_to_pytest(job), workflow


# --------------------------------------------------------------------------- #
# …and the LOCAL runner must not under-provision either.
# --------------------------------------------------------------------------- #
def test_the_local_runner_installs_everything_ci_does():
    """`sim/tests/run.sh` provisions its venv from `sim/tests/requirements.txt`. If that
    file lists less than CI installs, a local run silently under-provisions and the tests
    that need the missing package `importorskip` themselves away — a skip that reads as
    coverage, which is worse than a failure.

    This is not hypothetical. The file listed only pytest + playwright while the suite
    needs `paho-mqtt` (without it `sim/virtual_moxie.py` calls `sys.exit` at import, so
    the client-parity test fails), `jinja2` (the content renderer and its sandbox-escape
    probes) and `pyyaml` (the guards in this very file). Found 2026-09-03 when the
    live-gateway turn test skipped for a reason that had nothing to do with credentials.
    """
    req = open(os.path.join(os.path.dirname(__file__), "requirements.txt")).read()
    listed = {
        line.split("#")[0].strip().split(">")[0].split("=")[0].strip().lower()
        for line in req.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    # HERMETIC_TEST_DEPS is what BOTH tiers install, but the SIL job additionally
    # installs paho-mqtt — and that is the one whose absence bites hardest, because
    # `sim/virtual_moxie.py` calls sys.exit() at import rather than raising ImportError,
    # so the failure is a hard error in an unrelated-looking test. Check it explicitly.
    required = [d for d in HERMETIC_TEST_DEPS if not d.startswith("-r")]
    required.append("paho-mqtt")
    missing = [d for d in required if d.lower() not in listed]
    assert not missing, (
        "sim/tests/requirements.txt omits what CI installs: "
        + ", ".join(missing)
        + ". run.sh builds the local venv from that file, so the suite would run "
          "under-provisioned and skip instead of fail."
    )


def test_the_local_runner_reinstalls_when_requirements_change():
    """The old guard only rebuilt the venv when `pytest` was absent, so a venv holding
    pytest and nothing else was never repaired — exactly the state that produced the
    silent skips above. run.sh must key off the requirements file, not one binary."""
    run_sh = open(os.path.join(os.path.dirname(__file__), "run.sh")).read()
    assert "requirements.txt" in run_sh and "sha256sum" in run_sh, (
        "run.sh no longer re-installs when requirements.txt changes; a stale venv will "
        "under-provision the suite again")

# --------------------------------------------------------------------------- #
# Every live suite must be dispatched by SOME tier.
# --------------------------------------------------------------------------- #
def test_every_live_suite_is_dispatched_by_some_tier():
    """A live suite nobody runs is worse than one that does not exist: the file sits in
    the tree looking like coverage, and a status-log line starts claiming it.

    That is exactly what happened. `test_live_gateway_stt.py` and
    `test_live_telehealth_voice.py` were dispatched by **no** tier from the day they were
    written until 2026-09-03, while the log claimed live STT coverage. Nothing swept them
    in by accident either — the deep tier names FILES, so a `-k test_live_gateway`
    substring never applied to them. STT went live the same day as TTS and got the same
    claim but none of the enforcement.

    If a suite is deliberately not dispatched, add it to EXEMPT with the reason — that is
    a decision someone can read, unlike silence.
    """
    here = os.path.dirname(__file__)
    on_disk = {
        f[:-3] for f in os.listdir(here)
        if f.startswith("test_live_") and f.endswith(".py")
    }
    assert on_disk, "no live suites found — has the naming convention changed?"

    dispatched = set()
    for name in os.listdir(TEMPLATES):
        if not name.endswith((".yml", ".yaml")):
            continue
        text = open(os.path.join(TEMPLATES, name)).read()
        for suite in on_disk:
            # match the FILE the tier names, not a substring of a longer suite name
            if f"{suite}.py" in text:
                dispatched.add(suite)

    EXEMPT = {
        # test_live_gateway.py is named by the fast tier only to be --ignore'd there;
        # the deep tier really runs it, so it is dispatched and needs no exemption.
    }
    missing = sorted(on_disk - dispatched - set(EXEMPT))
    assert not missing, (
        "these live suites are dispatched by no CI tier, so they can only ever run on "
        "someone's laptop: " + ", ".join(missing) + ". Add them to the deep tier's "
        "creds-only invocation, or list them in EXEMPT with a reason."
    )


# --------------------------------------------------------------------------- #
# The split: the browser suites are a PARALLEL job, and the gate still requires it
# --------------------------------------------------------------------------- #
#
# PR #120 fixed a real defect — nine Chrome-launching suites had never executed in CI —
# but it put them inside `sil`, which already runs ~5,000 pytest tests against a real
# mosquitto broker. Measured consequence: the job went from ~7–8 min to ~17 min (runs at
# 11:32:56 → 11:49:28), and UNRELATED tests began failing. PR #125 is documentation-only
# and failed twice on two different SIL tests — `test_roster.py::…do_not_lose_each_others_
# robots` ("the roster write was refused") and `test_schedule_sil_e2e.py::…is_pinned_and_
# says_so` (`AssertionError: []`) — both of which pass locally, 3/3 and 2/2, in under a
# second. A docs-only change cannot break either; that is load contention on one runner.
#
# The suites therefore run in their own job. These guards hold the two properties that
# make the split real rather than cosmetic: it must actually be PARALLEL, and the merge
# gate must still be able to go red because of it.

GATE = os.path.join(REPO, "scripts", "pr-green.sh")


def _gate_source() -> str:
    with open(GATE) as fh:
        return fh.read()


def _required_jobs() -> list:
    """The `REQUIRED_JOBS=` line of the gate, parsed."""
    m = re.search(r'^REQUIRED_JOBS="([^"]*)"', _gate_source(), re.M)
    assert m, "scripts/pr-green.sh no longer declares REQUIRED_JOBS (update this guard)"
    return [s for s in m.group(1).split(",") if s]


def _gate_decision_script(tmp_path) -> str:
    """The gate's REAL decision block, lifted out of its heredoc so it can be executed.

    Restating the logic here would prove nothing about the script anybody actually runs —
    that is the whole lesson of a guard that reads a file instead of the wire.
    """
    src = _gate_source()
    body = re.search(r"<<'PY'\n(.*?)\nPY\n", src, re.S)
    assert body, "cannot find the gate's python block (update this guard)"
    p = os.path.join(str(tmp_path), "gate_decision.py")
    with open(p, "w") as fh:
        fh.write(body.group(1))
    return p


def test_the_browser_suites_run_in_their_own_job_in_parallel(fast):
    """No `needs:` — the split only buys wall-clock if the job starts when `sil` does."""
    jobs = fast["jobs"]
    browser = jobs.get("browser")
    assert browser is not None, "the fast tier has no `browser` job any more"
    for job_id, job in jobs.items():
        assert "needs" not in job, (
            f"job `{job_id}` declares `needs: {job.get('needs')}` — the fast tier's three "
            f"jobs are deliberately independent, so the tier costs max(), not sum()")


def test_no_job_runs_both_the_broker_suite_and_a_browser_suite(fast):
    """The regression this split exists to prevent, stated as an invariant.

    Re-adding one `node sim/test_csp.mjs` to `sil` would silently restore the contention
    (and the ~9 extra minutes) without anyone editing a comment.
    """
    browser_suites = {
        p for p in os.listdir(os.path.join(REPO, "sim"))
        if p.startswith("test_") and p.endswith(".mjs")
        and any(k in open(os.path.join(REPO, "sim", p), encoding="utf-8").read()
                for k in ("loadPuppeteer", "requireBrowser"))
    }
    assert browser_suites, "found no browser suites — has the harness API been renamed?"
    for job_id, job in fast["jobs"].items():
        runs = "\n".join(s.get("run") or "" for s in _steps(job))
        heavy = "pytest sim/tests" in runs or "run_smoke.sh" in runs
        here = sorted(s for _, s in _node_steps(job)
                      if os.path.basename(s) in browser_suites)
        assert not (heavy and here), (
            f"job `{job_id}` runs the broker-backed suite AND browser suites {here}. "
            f"That is the shape that took the SIL job to ~17 minutes and started "
            f"reddening documentation-only PRs; keep the browsers in their own job.")


def test_the_merge_gate_requires_every_job_in_the_fast_tier(fast):
    """A job the gate does not require is a suite that cannot redden the gate — the
    original bug wearing a new hat. So the list is checked in BOTH directions: no job
    unnamed, and no name that matches no job (a stale entry would look like coverage)."""
    names = {job_id: job["name"] for job_id, job in fast["jobs"].items()}
    required = _required_jobs()
    unrequired = sorted(f"{jid} ({n})" for jid, n in names.items()
                        if not any(req in n for req in required))
    assert not unrequired, (
        "these fast-tier jobs are in no REQUIRED_JOBS entry of scripts/pr-green.sh, so a "
        "PR could merge while they were absent from the rollup: " + ", ".join(unrequired))
    # Each entry must match EXACTLY ONE job. Matching none is a stale entry; matching two
    # is worse, because the gate's "is it in the rollup?" test is then satisfied by the
    # WRONG job and the right one can be absent. That is not hypothetical — the first cut
    # of this split named the new job "… (parallel with SIL)", so the `SIL` entry matched
    # the browser job and a rollup that had lost the broker job would have passed.
    for req in required:
        hits = sorted(f"{jid} ({n})" for jid, n in names.items() if req in n)
        assert len(hits) == 1, (
            f"scripts/pr-green.sh's required entry {req!r} matches {len(hits)} jobs in "
            f"sim/ci/ci.yml ({hits or 'none'}). One entry, one job: a stale entry proves "
            f"nothing, and an ambiguous one lets the wrong job satisfy the gate.")


def test_the_gate_actually_goes_RED_when_the_browser_job_is_missing_or_failing(fast, tmp_path):
    """Teeth, run against the gate's own decision code rather than a restatement of it.

    Four rollups, one property each: a complete green rollup passes; the same rollup with
    the browser job absent fails (the case `MIN` alone would have missed, since two of the
    three remaining checks still met the old default); the browser job still running fails;
    and the browser job red fails. If any of those passed, the split would have handed the
    repo a job whose result nobody has to wait for.
    """
    import subprocess

    script = _gate_decision_script(tmp_path)
    names = [job["name"] for job in fast["jobs"].values()]
    assert len(names) >= 3, names
    browser = next(n for n in names if "Browser" in n)

    def rollup(**over):
        out = []
        for n in names:
            r = {"name": n, "status": "COMPLETED", "conclusion": "SUCCESS"}
            r.update(over.get(n, {}))
            out.append(r)
        return [r for r in out if r.get("conclusion") != "__ABSENT__"]

    def run(rs, need="3"):
        return subprocess.run(
            [sys.executable, script, json.dumps(rs), need, ",".join(_required_jobs())],
            capture_output=True, text=True)

    green = run(rollup())
    assert green.returncode == 0, green.stdout + green.stderr

    # MISSING, twice. With the default floor the count clause happens to catch it, which
    # is luck rather than design — three jobs minus one is two, and the floor is three. So
    # the second call sets the floor to 1, isolating the by-NAME clause: that is the one
    # that has to carry the weight, because the floor stops helping the moment the tier
    # gains a fourth check (a Pages preview deploy already adds one on some PRs).
    absent = run(rollup(**{browser: {"conclusion": "__ABSENT__"}}))
    assert absent.returncode != 0, (
        "the gate passed a rollup with the browser job MISSING — a suite that cannot "
        "redden the gate is exactly the bug this split had to avoid:\n" + absent.stdout)

    absent_no_floor = run(rollup(**{browser: {"conclusion": "__ABSENT__"}}), need="1")
    assert absent_no_floor.returncode != 0, (
        "with the count floor lowered, the gate passed a rollup that never listed the "
        "browser job at all — the by-name requirement is doing nothing:\n"
        + absent_no_floor.stdout)
    assert "Browser" in absent_no_floor.stdout, absent_no_floor.stdout

    running = run(rollup(**{browser: {"status": "IN_PROGRESS", "conclusion": None}}))
    assert running.returncode != 0, (
        "the gate passed while the browser job was still running:\n" + running.stdout)

    red = run(rollup(**{browser: {"conclusion": "FAILURE"}}))
    assert red.returncode != 0, (
        "the gate passed with the browser job RED:\n" + red.stdout)
