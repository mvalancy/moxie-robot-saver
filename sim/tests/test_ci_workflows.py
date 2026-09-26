"""
The CI harness, guarded as code: a red push that a PR check never saw is an unproven
assumption about CI that nothing in the repo asserted.

The post-mortem behind it: a test reddened every `dev` push while the merge gate believed
the matching pull_request runs were green — they had failed identically, but PRs were
merged minutes after opening, before the slow `sil` job finished, and "no conclusion yet"
read as "not failing". Two invariants follow and live here:

* **Push and pull_request execute the same thing** in the fast tier (no `if:`, no
  `paths:` filter, no `concurrency:` group), so the two outcomes can never legitimately
  differ.
* **The installed workflows equal their templates.** `.github/workflows/*.yml` needs a
  workflow-scoped token to push, so the source lives at `sim/ci/*.yml` and is copied by
  hand — a drift waiting to happen, invisible until CI behaves unlike the file we read.

Pure file/YAML reading — no network, no `gh`, no runner.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys

import pytest

yaml = pytest.importorskip("yaml", reason="the workflow guards parse YAML")

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HERE = os.path.dirname(__file__)
TEMPLATES = os.path.join(REPO, "sim", "ci")
INSTALLED = os.path.join(REPO, ".github", "workflows")

#: Every workflow this repo ships. Only the fast tier is held to event symmetry; the
#: deep tier's extra jobs, the release tag trigger and the monitors' schedules
#: (`deployed.yml`, `promotion.yml` — gating nothing) are deliberate. Every workflow must
#: be named here, because the byte-identity guard is the only thing keeping template and
#: installed copy equal; the pytest-keyed guards simply pass over workflows without pytest.
TIERS = ("ci.yml", "ci-deep.yml", "release.yml", "deployed.yml", "promotion.yml",
         "cleanup.yml")
FAST = "ci.yml"
NON_RELEASE = ("ci.yml", "ci-deep.yml", "deployed.yml", "promotion.yml", "cleanup.yml")


def _load(path: str) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh)


def _template(name: str) -> dict:
    return _load(os.path.join(TEMPLATES, name))


def _triggers(doc: dict) -> dict:
    """The `on:` block — PyYAML reads bare `on` as the YAML 1.1 boolean `True`."""
    return doc.get("on", doc.get(True)) or {}


def _steps(job: dict) -> list:
    return list(job.get("steps") or [])


def _run(step: dict) -> str:
    return step.get("run") or ""


def _first(steps, pred):
    return next((i for i, s in enumerate(steps) if pred(s)), None)


def _browser_install(steps):
    at = _first(steps, lambda s: "playwright install" in _run(s))
    assert at is not None, "the sil job no longer installs a browser (update this guard)"
    return at


@pytest.fixture(scope="module")
def fast() -> dict:
    return _template(FAST)


# --------------------------------------------------------------------------- #
# The templates and the installed workflows are the same bytes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", TIERS)
def test_the_installed_workflow_is_byte_identical_to_its_template(name):
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
    """A workflow only under `.github/` cannot be edited without workflow scope."""
    installed = {f for f in os.listdir(INSTALLED) if f.endswith((".yml", ".yaml"))}
    templated = {f for f in os.listdir(TEMPLATES) if f.endswith((".yml", ".yaml"))}
    assert installed <= templated, sorted(installed - templated)


def test_ci_runs_do_not_publish_actions_artifacts():
    """CI verdicts live in checks and logs, not in durable artifacts nobody consumes.
    Release assets (softprops/action-gh-release in release.yml) are out of scope."""
    offenders = [f"{workflow}:{job_id}:{step.get('name', step.get('uses', ''))}"
                 for workflow in NON_RELEASE
                 for job_id, job in _template(workflow)["jobs"].items()
                 for step in _steps(job)
                 if step.get("uses", "").startswith("actions/upload-artifact@")]
    assert not offenders, (
        "non-release workflows must not create durable Actions artifacts: "
        + ", ".join(offenders))


def test_buildx_diagnostic_record_uploads_are_disabled():
    """build-push-action uploads .dockerbuild records unless explicitly disabled — the
    source of almost all accumulated artifacts — so every Buildx workflow must opt out."""
    found = []
    for workflow in TIERS:
        doc = _template(workflow)
        build_jobs = [job_id for job_id, job in doc["jobs"].items()
                      for step in _steps(job)
                      if step.get("uses", "").startswith("docker/build-push-action@")]
        if not build_jobs:
            continue
        found.extend((workflow, job_id) for job_id in build_jobs)
        value = str((doc.get("env") or {}).get("DOCKER_BUILD_RECORD_UPLOAD", "")).lower()
        assert value == "false", (
            f"{workflow} invokes docker/build-push-action but does not set "
            "DOCKER_BUILD_RECORD_UPLOAD=false")
    assert found, "the Buildx artifact guard found no docker/build-push-action steps"


def test_closed_pr_cleanup_deletes_only_that_prs_cache_namespace():
    """Closed PR caches cannot be restored by another ref; branch caches still can."""
    doc = _template("cleanup.yml")
    assert _triggers(doc) == {"pull_request": {"types": ["closed"]}}
    assert doc["permissions"] == {"contents": "read", "actions": "write"}
    steps = _steps(doc["jobs"]["cleanup"])
    assert len(steps) == 1
    step = steps[0]
    assert step["env"]["PR_REF"] == (
        "${{ format('refs/pull/{0}/merge', github.event.pull_request.number) }}")
    assert step["env"]["REPOSITORY"] == "${{ github.repository }}"
    assert step["run"] == (
        'gh cache delete --repo "$REPOSITORY" --all --ref "$PR_REF" '
        '--succeed-on-no-caches')
    assert "${{" not in step["run"], "event data must reach the shell only through env"


# --------------------------------------------------------------------------- #
# The fast tier runs the SAME thing on a push and on a pull request
# --------------------------------------------------------------------------- #
def test_the_fast_tier_fires_on_push_and_pull_request_for_the_same_branches(fast):
    on = _triggers(fast)
    assert set(on) == {"push", "pull_request"}, sorted(on)
    assert on["push"]["branches"] == on["pull_request"]["branches"], on
    assert on["push"]["branches"] == ["dev"], on["push"]


def test_no_job_or_step_in_the_fast_tier_is_conditional_at_all(fast):
    """One `if: github.event_name == 'push'` would make a green PR check and a red push
    *legitimate*, and the merge gate could not tell that from a race."""
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
    """The other two ways one commit can produce two different verdicts."""
    for event, spec in _triggers(fast).items():
        assert isinstance(spec, dict), (event, spec)
        assert not (set(spec) - {"branches"}), (
            f"{event} carries a filter beyond `branches`: {sorted(set(spec) - {'branches'})}")
    assert "concurrency" not in fast, fast.get("concurrency")
    for job_id, job in fast["jobs"].items():
        assert "concurrency" not in job, (job_id, job.get("concurrency"))


def test_the_fast_tier_runs_the_whole_pytest_suite(fast):
    """Somewhere the fast tier runs ALL of `sim/tests` with no `-k`/`--ignore`. Reads the
    commands with comments stripped: a comment explaining the no-selector rule once
    contained the very token this guard searches for."""
    runs = [_uncommented(_run(s)) for job in fast["jobs"].values() for s in _steps(job)]
    pytest_runs = [r for r in runs if "pytest sim/tests" in r]
    assert pytest_runs, "the fast tier no longer runs the pytest suite at all"
    whole = [r for r in pytest_runs if " -k " not in r and "--ignore" not in r]
    assert whole, ("every fast-tier pytest invocation now filters the suite:\n"
                   + "\n---\n".join(pytest_runs))


def test_the_fast_tier_fails_before_a_two_minute_merge_gate_can_open(fast):
    """The timing half of the post-mortem: the hermetic suite runs BEFORE the browser
    install, so a red suite reports inside the window a merger actually waits."""
    steps = _steps(fast["jobs"]["sil"])
    early = _first(steps, lambda s: "pytest sim/tests" in _run(s))
    assert early is not None, "the sil job runs no pytest"
    browser = _browser_install(steps)
    assert early < browser, (
        f"the first pytest step (#{early + 1}) runs after the browser install "
        f"(#{browser + 1}); a hermetic failure would take minutes to surface")


def test_the_early_hermetic_step_installs_protobuf(fast):
    """Without protobuf the early step would `importorskip` past the compiled-proto
    oracle — the very test the post-mortem was about."""
    early = next(s for s in _steps(fast["jobs"]["sil"]) if "pytest sim/tests" in _run(s))
    assert "protobuf" in early["run"], (
        "the early hermetic step must install protobuf, or the pb2 oracle silently skips:\n"
        + early["run"])


# --------------------------------------------------------------------------- #
# The headless node tests are actually WIRED — a test CI never runs is not a test
# --------------------------------------------------------------------------- #
#: The node tests guarding what the hosted static site tells a visitor (the mode machine
#: and the page in every mode) and what its Pages Functions SPEND on a visitor's behalf
#: (proxy caps + key sweep, tickets, audio decode, STT caps, Turnstile, transport,
#: fallback voices). All hermetic — Functions imported as ES modules with a plain
#: `context.env` and stubbed `fetch`, no account, key, secret or microphone — so there is
#: no excuse for any to be missing from the fast tier.
STATIC_SITE_NODE_TESTS = (
    "sim/test_mode.mjs",
    "sim/test_env_hosted.mjs",
    "sim/test_demo_proxy.mjs",
    "sim/test_demo_tickets.mjs",
    "sim/test_wav_decode.mjs",
    "sim/test_demo_ears.mjs",
    "sim/test_turnstile.mjs",
    "sim/test_cloud_transport.mjs",
    "sim/test_fallback_coverage.mjs",
)

#: The subset that must report BEFORE anything downloads a browser.
EARLY_NODE_TESTS = (
    "sim/test_mode.mjs",
    "sim/test_demo_proxy.mjs",
    "sim/test_demo_tickets.mjs",
    "sim/test_wav_decode.mjs",
    "sim/test_demo_ears.mjs",
    "sim/test_turnstile.mjs",
    "sim/test_cloud_transport.mjs",
)


def _node_steps(job: dict) -> list:
    """(index, script) for every `node sim/<file>.mjs` invocation in the job."""
    return [(i, token) for i, step in enumerate(_steps(job))
            for token in _run(step).split()
            if token.startswith("sim/test_") and token.endswith(".mjs")]


def _tier_node_steps(tier: dict) -> list:
    """(job_id, index, script) across EVERY job — the browser suites live in a parallel
    job, so reading only `sil` would silently stop covering them."""
    return [(job_id, i, script) for job_id, job in tier["jobs"].items()
            for i, script in _node_steps(job)]


@pytest.mark.parametrize("script", STATIC_SITE_NODE_TESTS)
def test_the_fast_tier_runs_the_static_site_honesty_tests(fast, script):
    wired = {s: j for j, _, s in _tier_node_steps(fast)}
    assert script in wired, (
        f"{script} is not run by ANY job of the fast tier; the honest-indicator contract "
        f"would be unproven on every push. Wired scripts: {sorted(wired)}")


def test_every_node_test_the_fast_tier_names_actually_exists(fast):
    """A typo fails the job as "Cannot find module" (reads as a broken runner), and
    pre-wiring a suite a sibling branch has not landed guarantees a red `dev`."""
    missing = sorted({s for _, _, s in _tier_node_steps(fast)
                      if not os.path.exists(os.path.join(REPO, s))})
    assert not missing, missing


@pytest.mark.parametrize("script", EARLY_NODE_TESTS)
def test_the_hermetic_edge_tests_report_before_a_two_minute_merge_gate_can_open(fast, script):
    """Each takes about a second; behind the browser install a red mode machine or a
    leaked gateway key would surface minutes after a script could have merged it."""
    steps = _steps(fast["jobs"]["sil"])
    at = _first(steps, lambda s: script in _run(s))
    assert at is not None, f"the fast tier no longer runs {script}"
    browser = _browser_install(steps)
    assert at < browser, (
        f"{script} (step #{at + 1}) runs after the browser install "
        f"(#{browser + 1}); a hermetic failure would take minutes to surface")


def test_no_hermetic_edge_test_needs_a_gateway_key_or_a_cloudflare_account(fast):
    """Keeps the fast tier runnable on a fork and a secret out of CI: a step that needed
    a credential would silently skip on a fork, or put a key in a workflow file."""
    steps = [s for job in fast["jobs"].values() for s in _steps(job)]
    for script in STATIC_SITE_NODE_TESTS:
        step = next((s for s in steps if script in _run(s)), None)
        assert step is not None, f"{script} is not wired into the fast tier"
        run = _run(step)
        for forbidden in ("MOXIE_LLM_API_KEY", "DEMO_GATEWAY_API_KEY", "CLOUDFLARE_API_TOKEN",
                          "secrets.", "${{"):
            assert forbidden not in run, (
                f"the step running {script} references {forbidden!r}; these tests are "
                f"hermetic and must never need a credential:\n{run}")


def test_the_only_event_conditionals_in_the_deep_tier_are_the_dispatch_only_live_tiers():
    """The deep tier gates its live stages (gateway spend, fork-unsafe) on
    `workflow_dispatch`; any other event conditional must be a deliberate act."""
    for job_id, job in _template("ci-deep.yml")["jobs"].items():
        for i, step in enumerate(_steps(job), 1):
            cond = step.get("if")
            if not cond or "github.event" not in cond:
                continue
            assert "workflow_dispatch" in cond, (
                f"deep tier job {job_id} step {i} branches on the event without being "
                f"dispatch-only: {cond}")


# --------------------------------------------------------------------------- #
# ONE dependency declaration, and the guards that keep it the only one
# --------------------------------------------------------------------------- #
#: The single declaration of what the pytest suite needs, and the same plus the browser
#: driver. It used to be hand-written in five workflow steps, no two the same.
HERMETIC_REQS = os.path.join("sim", "tests", "requirements-hermetic.txt")
FULL_REQS = os.path.join("sim", "tests", "requirements.txt")

#: Import name → distribution name, where they differ.
MODULE_TO_DISTRIBUTION = {
    "paho": "paho-mqtt",
    "yaml": "pyyaml",
    "google": "protobuf",
    "faster_whisper": "faster-whisper",
    "piper": "piper-tts",
}

#: Imported but deliberately NOT in the test list: ~2 GB of local model wheels, installed
#: only by the deep tier's opt-in voice step; their suites `importorskip` and say why.
DELIBERATELY_OPTIONAL = {"piper-tts", "faster-whisper"}


def _requirements(rel_path: str) -> set:
    """Distribution names a requirements file declares, following `-r` transitively
    (relative to the referring file, as pip does). Specifiers/extras/markers stripped."""
    out, stack, seen = set(), [os.path.join(REPO, rel_path)], set()
    while stack:
        path = stack.pop()
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        assert os.path.exists(path), (
            f"a requirements file references {path}, which does not exist")
        for raw in open(path):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith(("-r", "--requirement")):
                stack.append(os.path.join(os.path.dirname(path), line.split(None, 1)[1]))
                continue
            name = re.split(r"[<>=!~\[;\s]", line, maxsplit=1)[0].strip().lower()
            if name:
                out.add(name)
    return out


def _uncommented(run: str) -> str:
    """A `run:` block with shell comments removed — guards assert over commands, and
    these steps document at length what they no longer do."""
    out = []
    for line in (run or "").splitlines():
        stripped = line.split(" #", 1)[0]
        if not stripped.lstrip().startswith("#"):
            out.append(stripped)
    return "\n".join(out)


def _pip_tokens(run: str) -> set:
    """Package names a `run:` block's `pip install` lines name, comments excluded."""
    names = set()
    for line in _uncommented(run).splitlines():
        if "pip install" not in line:
            continue
        for token in line.split():
            token = token.strip('"\'\\')
            if not token or token.startswith("-") or token in ("pip", "install",
                                                             "python", "python3", "-m"):
                continue
            if token.endswith(".txt") or "/" in token:
                continue
            names.add(re.split(r"[<>=!~\[]", token, maxsplit=1)[0].strip().lower())
    return names


def _jobs_running_the_suite(workflow: str):
    """(job_id, steps, index-of-first-pytest) for every job that runs pytest —
    DISCOVERED, so a new or renamed job cannot escape the guards below."""
    for job_id, job in _template(workflow)["jobs"].items():
        steps = _steps(job)
        at = _first(steps, lambda s: "pytest sim/tests" in _uncommented(_run(s)))
        if at is not None:
            yield job_id, steps, at


def _installs_the_list(step) -> bool:
    run = _uncommented(_run(step))
    return HERMETIC_REQS in run or FULL_REQS in run


@pytest.mark.parametrize("workflow", TIERS)
def test_every_job_that_runs_the_pytest_suite_installs_the_declared_test_list(workflow):
    """A job running `pytest sim/tests` must install the one list first, so no tier can
    have "its own" deps to be missing."""
    for job_id, steps, at in _jobs_running_the_suite(workflow):
        before = "\n".join(_uncommented(_run(s)) for s in steps[:at + 1])
        assert HERMETIC_REQS in before or FULL_REQS in before, (
            f"{workflow} job `{job_id}` runs the pytest suite without installing "
            f"{HERMETIC_REQS} (or {FULL_REQS}) first, so its dependencies are whatever "
            f"that job happens to have:\n{before}")


def test_some_job_actually_runs_the_suite_so_the_guard_above_is_not_vacuous():
    found = {(w, j) for w in TIERS for j, _, _ in _jobs_running_the_suite(w)}
    assert (FAST, "sil") in found, found
    assert ("ci-deep.yml", "hil-sim") in found, found


@pytest.mark.parametrize("workflow", TIERS)
def test_no_job_redeclares_a_package_the_test_list_owns(workflow):
    """Declared once: after a job installs the list, no later step may `pip install` a
    package it names — a second declaration is a second chance to disagree (a hand-added
    `numpy` once hid that both hermetic tiers lacked it)."""
    owned = _requirements(FULL_REQS)
    for job_id, steps, _ in _jobs_running_the_suite(workflow):
        installed_at = _first(steps, _installs_the_list)
        if installed_at is None:
            continue                     # the guard above is the one that fails for this
        for i, step in enumerate(steps[installed_at:], start=installed_at):
            duplicates = _pip_tokens(_run(step)) & owned
            assert not duplicates, (
                f"{workflow} job `{job_id}` step #{i + 1} "
                f"({step.get('name', '?')}) re-installs {sorted(duplicates)}, which "
                f"{FULL_REQS} already declares; delete the line and let the list own it.")


def test_the_full_test_list_is_the_hermetic_list_plus_a_browser():
    """The two files differ by exactly the playwright wheel; anything more and they have
    become two lists again."""
    hermetic, full = _requirements(HERMETIC_REQS), _requirements(FULL_REQS)
    assert hermetic <= full, sorted(hermetic - full)
    assert full - hermetic == {"playwright"}, (
        f"{FULL_REQS} and {HERMETIC_REQS} now differ by more than the browser driver: "
        f"{sorted(full - hermetic)}. Move the package into the hermetic list (both tiers "
        f"need it) or say in this guard why the browser tier alone does.")


def _third_party_modules_the_suite_imports() -> dict:
    """{distribution: [files]} for every non-stdlib, non-local module `sim/tests` imports —
    EVERY import (function-level too: `numpy` hid inside a helper's functions) plus every
    `pytest.importorskip` name. "Local" = any module name matching a `.py` file or a
    package directory in the repo; coarse, but its failure mode is a missed check."""
    local, tests = set(), os.path.join(REPO, "sim", "tests")
    skip = {".git", ".venv", "node_modules", "__pycache__", "work"}
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".venv")]
        local.update(f[:-3] for f in files if f.endswith(".py"))
        local.update(d for d in dirs
                     if any(x.endswith(".py") for x in os.listdir(os.path.join(root, d))))
    found = {}
    for name in sorted(os.listdir(tests)):
        if not name.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(tests, name)).read())
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "importorskip" and node.args
                  and isinstance(node.args[0], ast.Constant)
                  and isinstance(node.args[0].value, str)):
                modules.add(node.args[0].value.split(".")[0])
        for module in modules:
            if module in sys.stdlib_module_names or module in local:
                continue
            dist = MODULE_TO_DISTRIBUTION.get(module, module).lower()
            found.setdefault(dist, []).append(name)
    return found


def test_the_import_scan_found_the_packages_we_know_the_suite_needs():
    """Anti-vacuity: `numpy` is reachable only through a helper's in-function import."""
    found = _third_party_modules_the_suite_imports()
    for known in ("pytest", "numpy", "pyyaml", "paho-mqtt", "fastapi"):
        assert known in found, (known, sorted(found))


def test_the_declared_test_list_covers_every_package_the_suite_imports():
    """A package a collectible test needs but its tier lacks turns an assertion into an
    `importorskip` that reads as a pass (or a mid-turn ModuleNotFoundError)."""
    declared = _requirements(FULL_REQS)
    missing = {dist: sorted(files)
               for dist, files in _third_party_modules_the_suite_imports().items()
               if dist not in declared and dist not in DELIBERATELY_OPTIONAL}
    assert not missing, (
        f"the suite imports packages that {FULL_REQS} does not declare, so every tier "
        f"runs those tests under-provisioned: {missing}. Add them to "
        f"{HERMETIC_REQS} (both tiers need it) or to DELIBERATELY_OPTIONAL with the "
        f"reason.")


def test_the_console_round_trip_suite_can_actually_run_in_ci():
    """`test_console_roundtrip.py` gates on fastapi; without the console's requirements
    in the list the whole file is one green skip (which it once was, for weeks)."""
    gate = open(os.path.join(HERE, "test_console_roundtrip.py")).read()
    assert 'importorskip("fastapi"' in gate, (
        "test_console_roundtrip.py no longer gates on fastapi — update this guard")
    declared = _requirements(HERMETIC_REQS)
    for dep in ("fastapi", "httpx"):
        assert dep in declared, (dep, sorted(declared))


def test_the_test_list_carries_protobuf_for_the_compiled_proto_oracle():
    assert "protobuf" in _requirements(HERMETIC_REQS)


def _run_sh() -> str:
    return open(os.path.join(HERE, "run.sh")).read()


def test_the_local_runner_installs_everything_ci_does():
    """`run.sh` provisions from `requirements.txt` (what CI installs) and its venv stamp
    must hash BOTH files, or moving a package between them leaves venvs stale."""
    run_sh = _run_sh()
    assert "-r \"$here/requirements.txt\"" in run_sh, (
        "run.sh no longer provisions its venv from requirements.txt")
    for name in ("requirements.txt", "requirements-hermetic.txt"):
        assert name in run_sh.split("sha256sum", 1)[1].split("\n")[0] or \
            name in run_sh, f"run.sh does not hash {name}; a change to it leaves venvs stale"
    stamp = [l for l in run_sh.splitlines() if "sha256sum" in l]
    assert stamp and "requirements-hermetic.txt" in "\n".join(stamp), (
        "run.sh's venv stamp does not cover requirements-hermetic.txt, where the packages "
        f"actually live:\n{stamp}")


def test_the_local_runner_reinstalls_when_requirements_change():
    """Keyed on the requirements, not on `pytest` being present — a venv holding pytest
    and nothing else was never repaired."""
    run_sh = _run_sh()
    assert "requirements.txt" in run_sh and "sha256sum" in run_sh, (
        "run.sh no longer re-installs when requirements.txt changes; a stale venv will "
        "under-provision the suite again")


def test_the_agent_brief_protocol_points_at_the_declared_test_list():
    """Agent briefs are copied from the orchestration plan's protocol; a hand-listed venv
    recipe there started every agent with a red suite. The status log may quote history."""
    plan = open(os.path.join(REPO, "docs", "architecture", "orchestration-plan.md")).read()
    protocol = plan.split("## Status log", 1)[0]
    assert "sim/tests/requirements.txt" in protocol, (
        "the orchestration plan's agent protocol does not name sim/tests/requirements.txt; "
        "a brief written from it will hand-list packages and omit one")
    offenders = [line.strip() for line in protocol.splitlines()
                 if "pip install" in line and "pytest" in line
                 and "sim/tests/requirements" not in line]
    assert not offenders, (
        "the plan's protocol hand-lists test dependencies instead of pointing at the one "
        f"declaration: {offenders}")


def test_every_live_suite_is_dispatched_by_some_tier():
    """A live suite nobody runs sits in the tree looking like coverage. The deep tier
    names FILES, so a `-k` substring never sweeps one in. A deliberately undispatched
    suite goes in EXEMPT with its reason."""
    on_disk = {f[:-3] for f in os.listdir(HERE)
               if f.startswith("test_live_") and f.endswith(".py")}
    assert on_disk, "no live suites found — has the naming convention changed?"
    texts = [open(os.path.join(TEMPLATES, n)).read() for n in os.listdir(TEMPLATES)
             if n.endswith((".yml", ".yaml"))]
    # match the FILE the tier names, not a substring of a longer suite name
    dispatched = {suite for suite in on_disk for t in texts if f"{suite}.py" in t}
    EXEMPT = {}
    missing = sorted(on_disk - dispatched - set(EXEMPT))
    assert not missing, (
        "these live suites are dispatched by no CI tier, so they can only ever run on "
        "someone's laptop: " + ", ".join(missing) + ". Add them to the deep tier's "
        "creds-only invocation, or list them in EXEMPT with a reason.")


# --------------------------------------------------------------------------- #
# The browser suites are a PARALLEL job, and the gate still requires it
# --------------------------------------------------------------------------- #
# Chrome suites inside `sil` (~5,000 broker-backed tests) more than doubled its runtime
# and reddened unrelated tests through load contention. The split is real only if the
# job is actually parallel and the merge gate can still go red because of it.

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
    """The gate's REAL decision block, lifted out of its heredoc so it can be executed —
    restating the logic here would prove nothing about the script anybody runs."""
    body = re.search(r"<<'PY'\n(.*?)\nPY\n", _gate_source(), re.S)
    assert body, "cannot find the gate's python block (update this guard)"
    p = os.path.join(str(tmp_path), "gate_decision.py")
    with open(p, "w") as fh:
        fh.write(body.group(1))
    return p


def test_the_browser_suites_run_in_their_own_job_in_parallel(fast):
    """No `needs:` — the split only buys wall-clock if the job starts when `sil` does."""
    assert fast["jobs"].get("browser") is not None, "the fast tier has no `browser` job any more"
    for job_id, job in fast["jobs"].items():
        assert "needs" not in job, (
            f"job `{job_id}` declares `needs: {job.get('needs')}` — the fast tier's "
            f"jobs are deliberately independent, so the tier costs max(), not sum()")


def test_no_job_runs_both_the_broker_suite_and_a_browser_suite(fast):
    """Re-adding one browser suite to `sil` would silently restore the contention."""
    browser_suites = {
        p for p in os.listdir(os.path.join(REPO, "sim"))
        if p.startswith("test_") and p.endswith(".mjs")
        and any(k in open(os.path.join(REPO, "sim", p), encoding="utf-8").read()
                for k in ("loadPuppeteer", "requireBrowser"))
    }
    assert browser_suites, "found no browser suites — has the harness API been renamed?"
    for job_id, job in fast["jobs"].items():
        runs = "\n".join(_run(s) for s in _steps(job))
        heavy = "pytest sim/tests" in runs or "run_smoke.sh" in runs
        here = sorted(s for _, s in _node_steps(job)
                      if os.path.basename(s) in browser_suites)
        assert not (heavy and here), (
            f"job `{job_id}` runs the broker-backed suite AND browser suites {here}; "
            f"keep the browsers in their own job.")


def test_the_merge_gate_requires_every_job_in_the_fast_tier(fast):
    """Checked both ways: a job the gate does not require cannot redden it, and each
    entry must match EXACTLY one job — none is stale, two lets the wrong job satisfy the
    gate while the right one is absent."""
    names = {job_id: job["name"] for job_id, job in fast["jobs"].items()}
    required = _required_jobs()
    unrequired = sorted(f"{jid} ({n})" for jid, n in names.items()
                        if not any(req in n for req in required))
    assert not unrequired, (
        "these fast-tier jobs are in no REQUIRED_JOBS entry of scripts/pr-green.sh, so a "
        "PR could merge while they were absent from the rollup: " + ", ".join(unrequired))
    for req in required:
        hits = sorted(f"{jid} ({n})" for jid, n in names.items() if req in n)
        assert len(hits) == 1, (
            f"scripts/pr-green.sh's required entry {req!r} matches {len(hits)} jobs in "
            f"sim/ci/ci.yml ({hits or 'none'}). One entry, one job.")


def test_the_gate_actually_goes_RED_when_the_browser_job_is_missing_or_failing(fast, tmp_path):
    """Against the gate's own decision code: a green rollup passes; the browser job
    absent, still running, or red each fails."""
    script = _gate_decision_script(tmp_path)
    names = [job["name"] for job in fast["jobs"].values()]
    assert len(names) >= 3, names
    browser = next(n for n in names if "Browser" in n)

    def rollup(**over):
        out = [dict({"name": n, "status": "COMPLETED", "conclusion": "SUCCESS"},
                    **over.get(n, {})) for n in names]
        return [r for r in out if r.get("conclusion") != "__ABSENT__"]

    def run(rs, need="3"):
        return subprocess.run(
            [sys.executable, script, json.dumps(rs), need, ",".join(_required_jobs())],
            capture_output=True, text=True)

    green = run(rollup())
    assert green.returncode == 0, green.stdout + green.stderr

    # MISSING, twice: the default count floor catches it by luck, so the second call
    # lowers the floor to 1 to isolate the by-NAME clause that must carry the weight.
    absent = run(rollup(**{browser: {"conclusion": "__ABSENT__"}}))
    assert absent.returncode != 0, (
        "the gate passed a rollup with the browser job MISSING:\n" + absent.stdout)
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
    assert red.returncode != 0, "the gate passed with the browser job RED:\n" + red.stdout
