"""The CI harness, guarded as code (pure YAML/file reading — no network, `gh` or runner).

Post-mortem: a test reddened every `dev` push while PRs merged minutes after opening,
before the slow `sil` job concluded — "no conclusion yet" read as "not failing". So: push
and pull_request run the SAME fast tier, a red hermetic suite reports before a two-minute
merge gate can open, the gate requires every job, and the installed workflows equal their
`sim/ci/` templates (`.github/workflows/` needs a workflow-scoped token to push).
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
TIERS = sorted(f for f in os.listdir(TEMPLATES) if f.endswith((".yml", ".yaml")))
FAST = "ci.yml"


def _template(name: str) -> dict:
    with open(os.path.join(TEMPLATES, name)) as fh:
        return yaml.safe_load(fh)


def _triggers(doc: dict) -> dict:
    """The `on:` block — PyYAML reads bare `on` as the YAML 1.1 boolean `True`."""
    return doc.get("on", doc.get(True)) or {}


def _steps(job: dict) -> list:
    return list(job.get("steps") or [])


def _uncommented(run) -> str:
    """A `run:` block without shell comments — these steps document at length what they
    no longer do, and a guard must read commands, not prose."""
    return "\n".join(ln.split(" #", 1)[0] for ln in (run or "").splitlines()
                     if not ln.lstrip().startswith("#"))


def _run(step: dict) -> str:
    return _uncommented(step.get("run"))


def _first(steps, pred):
    return next((i for i, s in enumerate(steps) if pred(s)), None)


@pytest.fixture(scope="module")
def fast() -> dict:
    return _template(FAST)


# ------------------------------------------------ templates == installed workflows --
def test_every_workflow_is_installed_byte_identical_to_its_template():
    installed = {f for f in os.listdir(INSTALLED) if f.endswith((".yml", ".yaml"))}
    assert installed == set(TIERS), ("template-only or installed-only workflows",
                                     sorted(installed ^ set(TIERS)))
    drifted = [n for n in TIERS
               if open(os.path.join(TEMPLATES, n), "rb").read()
               != open(os.path.join(INSTALLED, n), "rb").read()]
    assert not drifted, f"{drifted}: copy sim/ci/ over .github/workflows/ in the same commit"


def test_ci_publishes_no_durable_actions_artifacts():
    """Verdicts live in checks and logs; Buildx uploads `.dockerbuild` records unless told
    not to (the source of almost all accumulated artifacts). Release assets are exempt."""
    for workflow in TIERS:
        doc = _template(workflow)
        uses = [s.get("uses", "") for j in doc["jobs"].values() for s in _steps(j)]
        if workflow != "release.yml":
            assert not any(u.startswith("actions/upload-artifact@") for u in uses), workflow
        if any(u.startswith("docker/build-push-action@") for u in uses):
            assert str((doc.get("env") or {}).get("DOCKER_BUILD_RECORD_UPLOAD")).lower() \
                == "false", f"{workflow}: set DOCKER_BUILD_RECORD_UPLOAD=false"


def test_closed_pr_cleanup_deletes_only_that_prs_cache_namespace():
    doc = _template("cleanup.yml")
    assert _triggers(doc) == {"pull_request": {"types": ["closed"]}}
    assert doc["permissions"] == {"contents": "read", "actions": "write"}
    (step,) = _steps(doc["jobs"]["cleanup"])
    assert step["env"]["PR_REF"].endswith("format('refs/pull/{0}/merge', "
                                          "github.event.pull_request.number) }}")
    assert '--ref "$PR_REF"' in step["run"]
    assert "${{" not in step["run"], "event data must reach the shell only through env"


# ------------------------------------ push and pull_request run the SAME fast tier --
def test_the_fast_tier_fires_on_push_and_pull_request_for_the_same_branches(fast):
    on = _triggers(fast)
    assert set(on) == {"push", "pull_request"}, sorted(on)
    assert on["push"] == on["pull_request"] == {"branches": ["dev"]}, \
        "no paths:/filters — one commit must not get two verdicts"
    assert "concurrency" not in fast and not any("concurrency" in j for j in fast["jobs"].values())


def test_no_job_or_step_in_the_fast_tier_is_conditional_at_all(fast):
    """One `if: github.event_name == 'push'` makes a green PR and a red push legitimate."""
    offenders = [f"{jid}: {s.get('name', '?')}" for jid, job in fast["jobs"].items()
                 for s in [job, *_steps(job)] if "if" in s]
    assert not offenders, offenders


def test_the_fast_tier_needs_no_credential_or_expression(fast):
    """Runnable on a fork, and no secret can reach a workflow file."""
    text = open(os.path.join(TEMPLATES, FAST)).read()
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "secrets." not in code and "${{" not in code


def test_the_fast_tier_runs_the_whole_pytest_suite(fast):
    runs = [_run(s) for job in fast["jobs"].values() for s in _steps(job)]
    whole = [r for r in runs if "pytest sim/tests" in r and " -k " not in r and "--ignore" not in r]
    assert whole, "every fast-tier pytest invocation filters the suite"


#: Hermetic node suites guarding what the hosted site shows and SPENDS; ~1 s each.
EARLY_NODE_TESTS = ("sim/test_mode.mjs", "sim/test_demo_proxy.mjs", "sim/test_demo_tickets.mjs",
                    "sim/test_wav_decode.mjs", "sim/test_demo_ears.mjs", "sim/test_turnstile.mjs",
                    "sim/test_cloud_transport.mjs", "sim/test_fallback_coverage.mjs")


@pytest.mark.parametrize("needle", ("pytest sim/tests",) + EARLY_NODE_TESTS)
def test_hermetic_checks_report_before_the_browser_install(fast, needle):
    """Behind the ~3-minute browser install a red suite surfaces after a merge gate opens."""
    steps = _steps(fast["jobs"]["sil"])
    at = _first(steps, lambda s: needle in _run(s))
    browser = _first(steps, lambda s: "playwright install" in _run(s))
    assert at is not None and browser is not None, (needle, at, browser)
    assert at < browser, f"{needle} runs at step #{at + 1}, after the browser install"


def test_every_node_test_the_fast_tier_names_actually_exists(fast):
    """A typo fails as "Cannot find module" (reads as a broken runner)."""
    named = {t for job in fast["jobs"].values() for s in _steps(job) for t in _run(s).split()
             if t.startswith("sim/test_") and t.endswith(".mjs")}
    assert named and not [s for s in named if not os.path.exists(os.path.join(REPO, s))]


def test_the_only_event_conditionals_in_the_deep_tier_are_the_dispatch_only_live_tiers():
    """Live stages spend gateway money and are fork-unsafe: dispatch-only."""
    for job_id, job in _template("ci-deep.yml")["jobs"].items():
        for step in _steps(job):
            cond = step.get("if") or ""
            assert "github.event" not in cond or "workflow_dispatch" in cond, (job_id, cond)


# -------------------------------------------- ONE dependency declaration ----------
HERMETIC_REQS = "sim/tests/requirements-hermetic.txt"
FULL_REQS = "sim/tests/requirements.txt"
MODULE_TO_DISTRIBUTION = {"paho": "paho-mqtt", "yaml": "pyyaml", "google": "protobuf",
                          "faster_whisper": "faster-whisper", "piper": "piper-tts"}
#: ~2 GB of local model wheels, installed only by the deep tier's opt-in voice step.
DELIBERATELY_OPTIONAL = {"piper-tts", "faster-whisper"}


def _requirements(rel_path: str) -> set:
    """Distribution names a requirements file declares, following `-r` transitively."""
    out, stack, seen = set(), [os.path.join(REPO, rel_path)], set()
    while stack:
        path = stack.pop()
        if os.path.realpath(path) in seen:
            continue
        seen.add(os.path.realpath(path))
        for raw in open(path):
            line = raw.split("#", 1)[0].strip()
            if line.startswith(("-r", "--requirement")):
                stack.append(os.path.join(os.path.dirname(path), line.split(None, 1)[1]))
            elif line:
                out.add(re.split(r"[<>=!~\[;\s]", line, maxsplit=1)[0].lower())
    return out


def _pip_tokens(run: str) -> set:
    names = set()
    for line in run.splitlines():
        if "pip install" in line:
            for tok in (t.strip("\"'\\") for t in line.split()):
                if tok and not tok.startswith("-") and "/" not in tok and not tok.endswith(".txt") \
                        and tok not in ("pip", "install", "python", "python3"):
                    names.add(re.split(r"[<>=!~\[]", tok, maxsplit=1)[0].lower())
    return names


def _jobs_running_the_suite(workflow: str):
    """(job_id, steps, index-of-first-pytest), DISCOVERED so a new job cannot escape."""
    for job_id, job in _template(workflow)["jobs"].items():
        steps = _steps(job)
        at = _first(steps, lambda s: "pytest sim/tests" in _run(s))
        if at is not None:
            yield job_id, steps, at


def test_some_job_actually_runs_the_suite_so_the_guards_below_are_not_vacuous():
    found = {(w, j) for w in TIERS for j, _, _ in _jobs_running_the_suite(w)}
    assert {(FAST, "sil"), ("ci-deep.yml", "hil-sim")} <= found, found


@pytest.mark.parametrize("workflow", TIERS)
def test_no_job_redeclares_a_package_the_test_list_owns(workflow):
    """Every job running pytest installs the ONE list first, and no step re-installs a
    package it names — a hand-added `numpy` once hid that both hermetic tiers lacked it."""
    owned = _requirements(FULL_REQS)
    for job_id, steps, at in _jobs_running_the_suite(workflow):
        installed = _first(steps, lambda s: HERMETIC_REQS in _run(s) or FULL_REQS in _run(s))
        assert installed is not None and installed <= at, \
            f"{workflow}:{job_id} runs pytest without installing {HERMETIC_REQS} first"
        for step in steps[installed:]:
            dup = _pip_tokens(_run(step)) & owned
            assert not dup, f"{workflow}:{job_id} ({step.get('name')}) re-installs {dup}"


def test_the_full_test_list_is_the_hermetic_list_plus_a_browser():
    hermetic, full = _requirements(HERMETIC_REQS), _requirements(FULL_REQS)
    assert hermetic <= full and full - hermetic == {"playwright"}, sorted(full ^ hermetic)


def _third_party_modules_the_suite_imports() -> dict:
    """{distribution: [files]} for every non-stdlib, non-local module `sim/tests` imports,
    function-level imports and `pytest.importorskip` names included."""
    local, skip = set(), {".git", "node_modules", "__pycache__", "work"}
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".venv")]
        local.update(f[:-3] for f in files if f.endswith(".py"))
        local.update(d for d in dirs if any(x.endswith(".py")
                                            for x in os.listdir(os.path.join(root, d))))
    found = {}
    for name in sorted(os.listdir(HERE)):
        if not name.endswith(".py"):
            continue
        modules = set()
        for node in ast.walk(ast.parse(open(os.path.join(HERE, name)).read())):
            if isinstance(node, ast.Import):
                modules.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
            elif (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "importorskip"
                  and node.args and isinstance(node.args[0], ast.Constant)):
                modules.add(str(node.args[0].value).split(".")[0])
        for m in modules - set(sys.stdlib_module_names) - local:
            found.setdefault(MODULE_TO_DISTRIBUTION.get(m, m).lower(), []).append(name)
    return found


def test_the_declared_test_list_covers_every_package_the_suite_imports():
    """An undeclared package turns an assertion into an `importorskip` that reads as a pass
    (`numpy` hid in a helper's function-level import; `fastapi` skipped the console
    round-trip suite for weeks)."""
    found = _third_party_modules_the_suite_imports()
    assert {"pytest", "numpy", "pyyaml", "paho-mqtt", "fastapi", "protobuf"} <= set(found)
    declared = _requirements(HERMETIC_REQS)
    missing = {d: f for d, f in found.items()
               if d not in declared | {"playwright"} | DELIBERATELY_OPTIONAL}
    assert not missing, f"add to {HERMETIC_REQS} (or DELIBERATELY_OPTIONAL): {missing}"


def test_the_local_runner_reinstalls_when_either_requirements_file_changes():
    """`run.sh` provisions from requirements.txt and stamps its venv with BOTH files, or
    moving a package between them leaves venvs stale."""
    run_sh = open(os.path.join(HERE, "run.sh")).read()
    assert '-r "$here/requirements.txt"' in run_sh
    stamp = "\n".join(ln for ln in run_sh.splitlines() if "sha256sum" in ln)
    assert "requirements.txt" in stamp and "requirements-hermetic.txt" in stamp, stamp


def test_every_live_suite_is_dispatched_by_some_tier():
    """A live suite nobody runs sits in the tree looking like coverage."""
    on_disk = {f for f in os.listdir(HERE) if f.startswith("test_live_") and f.endswith(".py")}
    texts = "\n".join(open(os.path.join(TEMPLATES, n)).read() for n in TIERS)
    assert on_disk and not sorted(f for f in on_disk if f not in texts)


# ------------------------- the browser job is parallel, and the gate requires it --
GATE = os.path.join(REPO, "scripts", "pr-green.sh")


def _required_jobs() -> list:
    m = re.search(r'^REQUIRED_JOBS="([^"]*)"', open(GATE).read(), re.M)
    assert m, "scripts/pr-green.sh no longer declares REQUIRED_JOBS"
    return [s for s in m.group(1).split(",") if s]


def test_the_browser_suites_run_in_their_own_parallel_job(fast):
    """Chrome suites inside `sil` doubled its runtime and reddened unrelated tests through
    load contention; no `needs:`, so the tier costs max(), not sum()."""
    assert "browser" in fast["jobs"]
    assert not [j for j, job in fast["jobs"].items() if "needs" in job]
    browser_suites = {p for p in os.listdir(os.path.join(REPO, "sim"))
                      if p.startswith("test_") and p.endswith(".mjs")
                      and re.search(r"loadPuppeteer|requireBrowser",
                                    open(os.path.join(REPO, "sim", p), encoding="utf-8").read())}
    assert browser_suites
    for job_id, job in fast["jobs"].items():
        runs = "\n".join(_run(s) for s in _steps(job))
        if "pytest sim/tests" in runs or "run_smoke.sh" in runs:
            here = [s for s in browser_suites if f"sim/{s}" in runs]
            assert not here, f"`{job_id}` runs the broker suite AND browser suites {here}"


def test_the_merge_gate_requires_every_job_in_the_fast_tier(fast):
    """Each fast-tier job matches exactly one REQUIRED_JOBS entry and vice versa."""
    names = [job["name"] for job in fast["jobs"].values()]
    for n in names:
        assert any(req in n for req in _required_jobs()), f"{n!r} cannot redden the gate"
    for req in _required_jobs():
        assert sum(req in n for n in names) == 1, (req, names)


def test_the_gate_goes_red_when_the_browser_job_is_missing_running_or_failing(fast, tmp_path):
    """Against the gate's REAL decision block, lifted out of its heredoc."""
    body = re.search(r"<<'PY'\n(.*?)\nPY\n", open(GATE).read(), re.S)
    script = tmp_path / "gate.py"
    script.write_text(body.group(1))
    names = [job["name"] for job in fast["jobs"].values()]
    browser = next(n for n in names if "Browser" in n)

    def gate(over=None, need="3"):
        rs = [dict({"name": n, "status": "COMPLETED", "conclusion": "SUCCESS"},
                   **(over if n == browser and over else {})) for n in names]
        rs = [r for r in rs if r["conclusion"] != "ABSENT"]
        return subprocess.run([sys.executable, str(script), json.dumps(rs), need,
                               ",".join(_required_jobs())], capture_output=True, text=True)

    assert gate().returncode == 0
    # need=1 lowers the count floor so the by-NAME clause must carry the weight
    assert gate({"conclusion": "ABSENT"}, need="1").returncode != 0
    assert gate({"status": "IN_PROGRESS", "conclusion": None}).returncode != 0
    assert gate({"conclusion": "FAILURE"}).returncode != 0
