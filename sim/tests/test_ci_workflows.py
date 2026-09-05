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

import ast
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
    file was written about.

    Reads the COMMANDS, not the whole `run:` block. It used to read the block, and on
    2026-09-05 the whole-suite step grew a comment explaining that it deliberately runs
    with no selector — the phrase contains the very token this guard searches for, so a
    comment describing the guaranteed behaviour broke the guarantee. Playbook rule 17, in
    the file that documents rule 17's first instance: the guard was wrong, not the comment.
    """
    runs = [_uncommented(s.get("run", ""))
            for job in fast["jobs"].values() for s in _steps(job)]
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
# ONE dependency declaration, and the guards that keep it the only one
# --------------------------------------------------------------------------- #
#: The single place a package the pytest suite needs is named, and the same list plus the
#: browser driver. Read either file's header for the full post-mortem; the short version is
#: that this list used to be hand-written in FIVE workflow steps, no two of them the same,
#: and every difference was drift rather than a decision.
HERMETIC_REQS = os.path.join("sim", "tests", "requirements-hermetic.txt")
FULL_REQS = os.path.join("sim", "tests", "requirements.txt")

#: Import name → distribution name, for the handful where they differ. Everything else is
#: assumed to be its own distribution, which is true for the rest of what the suite imports.
MODULE_TO_DISTRIBUTION = {
    "paho": "paho-mqtt",
    "yaml": "pyyaml",
    "google": "protobuf",
    "faster_whisper": "faster-whisper",
    "piper": "piper-tts",
}

#: Modules the suite imports that must NOT be in the test list — each a decision, each with
#: its reason here, because an unexplained exception is how the coverage guard below would
#: quietly stop covering anything.
#:
#: `piper-tts` and `faster-whisper` are ~2 GB of local model wheels plus two 63 MB voices;
#: only the deep tier's opt-in `voice: true` step installs them, and the suites that need
#: them `importorskip` at module scope and say so in their skip reason.
DELIBERATELY_OPTIONAL = {"piper-tts", "faster-whisper"}


def _requirements(rel_path: str) -> set:
    """Distribution names a requirements file declares, following `-r` transitively.

    `-r` is resolved relative to the referring file, exactly as pip does, so
    `sim/tests/requirements-hermetic.txt` can point at `../../mqtt/requirements.txt` and
    the appliance's own dependencies stay owned by the appliance. Version specifiers,
    extras and environment markers are stripped: this answers "is this package declared",
    not "at which version".
    """
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
    """A `run:` block with its shell comments removed.

    Playbook rule 17 — a guard must assert over code, not over the whole file. These
    workflow steps explain at length what they no longer do ("`pip install protobuf` used
    to be the first line of this step"), so a duplicate-declaration guard that cannot tell
    a comment from a command would fire on the very comment that documents the fix. PR #52
    and the `functions/` json-import guard are the same lesson.
    """
    out = []
    for line in (run or "").splitlines():
        stripped = line.split(" #", 1)[0]
        if stripped.lstrip().startswith("#"):
            continue
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
    """(job_id, steps, index-of-first-pytest) for every job of the tier that runs pytest.

    DISCOVERED rather than listed. This used to be a two-entry `HERMETIC_JOBS` constant,
    which meant a third job that ran the suite — or a job renamed — was covered by nothing,
    the same "the guard silently stopped applying" shape as the browser-suite split.
    """
    doc = _load(os.path.join(TEMPLATES, workflow))
    for job_id, job in doc["jobs"].items():
        steps = _steps(job)
        at = next((i for i, s in enumerate(steps)
                   if "pytest sim/tests" in _uncommented(s.get("run") or "")), None)
        if at is not None:
            yield job_id, steps, at


@pytest.mark.parametrize("workflow", TIERS)
def test_every_job_that_runs_the_pytest_suite_installs_the_declared_test_list(workflow):
    """Playbook rule 9, but enforced on the DECLARATION rather than on a copy of it: a job
    that runs `pytest sim/tests` must have installed the one test list before it. Whichever
    packages that list names then arrive in every tier at once, and a tier can no longer
    have "its own" deps to be missing."""
    for job_id, steps, at in _jobs_running_the_suite(workflow):
        before = "\n".join(_uncommented(s.get("run") or "") for s in steps[:at + 1])
        assert HERMETIC_REQS in before or FULL_REQS in before, (
            f"{workflow} job `{job_id}` runs the pytest suite without installing "
            f"{HERMETIC_REQS} (or {FULL_REQS}) first, so its dependencies are whatever "
            f"that job happens to have — which is how five different hand-written lists "
            f"came about:\n{before}")


def test_some_job_actually_runs_the_suite_so_the_guard_above_is_not_vacuous():
    """A parametrized guard over a discovered set passes trivially when the set is empty,
    and "the discovery quietly returned nothing" is this repo's most common guard failure.
    So: the fast tier's `sil` and the deep tier's `hil-sim` must both be found."""
    found = {(w, j) for w in TIERS for j, _, _ in _jobs_running_the_suite(w)}
    assert (FAST, "sil") in found, found
    assert ("ci-deep.yml", "hil-sim") in found, found


@pytest.mark.parametrize("workflow", TIERS)
def test_no_job_redeclares_a_package_the_test_list_owns(workflow):
    """"Declared once" as an assertion. Once a job has installed the test list, no later
    step in it may `pip install` a package that list already names.

    Three lines died to this when it was written, and each had cost something: `paho-mqtt`
    in the fast tier's broker step, `protobuf` before the QR-parity step, and `numpy` in
    BOTH of the deep tier's live steps — the last being why the hermetic tiers never
    noticed they lacked it, since the only jobs that exercised the numpy tests installed it
    by hand a second time.
    """
    owned = _requirements(FULL_REQS)
    for job_id, steps, _ in _jobs_running_the_suite(workflow):
        installed_at = next(
            (i for i, s in enumerate(steps)
             if HERMETIC_REQS in _uncommented(s.get("run") or "")
             or FULL_REQS in _uncommented(s.get("run") or "")), None)
        if installed_at is None:
            continue                     # the guard above is the one that fails for this
        for i, step in enumerate(steps[installed_at:], start=installed_at):
            duplicates = _pip_tokens(step.get("run") or "") & owned
            assert not duplicates, (
                f"{workflow} job `{job_id}` step #{i + 1} "
                f"({step.get('name', '?')}) re-installs {sorted(duplicates)}, which "
                f"{FULL_REQS} already declares. Two declarations of a dependency are two "
                f"chances to disagree; delete the line and let the list own it.")


def test_the_full_test_list_is_the_hermetic_list_plus_a_browser():
    """The two files exist for exactly one difference — the ~35 MB playwright wheel, which
    the `-k "not test_sil and not test_docs"` runs deselect every user of. If they ever
    differ by anything else they have become two lists again, which is the whole defect."""
    hermetic, full = _requirements(HERMETIC_REQS), _requirements(FULL_REQS)
    assert hermetic <= full, sorted(hermetic - full)
    assert full - hermetic == {"playwright"}, (
        f"{FULL_REQS} and {HERMETIC_REQS} now differ by more than the browser driver: "
        f"{sorted(full - hermetic)}. Move the package into the hermetic list (both tiers "
        f"need it) or say in this guard why the browser tier alone does.")


def _third_party_modules_the_suite_imports() -> dict:
    """{distribution: [files]} for every non-stdlib, non-local module `sim/tests` imports.

    EVERY import, not only module-scope ones, and every `pytest.importorskip` name. That is
    the point: `numpy` was invisible to a module-scope-only reading of these files because
    `helpers_audio.py` imported it inside five FUNCTIONS, so the suite needed a package that
    no test file mentioned. A `getattr`-based import would still escape this, which is worth
    saying rather than pretending otherwise.

    "Local" is any module name that matches a `.py` file or a directory containing one
    anywhere in the repo — coarse, but the repo has no directory named after a package it
    depends on, and the failure mode is a missed check rather than a false alarm.
    """
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
    """Anti-vacuity for the guard below: a scan that came back empty would pass it. These
    five are certainties — `numpy` deliberately among them, since it is reachable only
    through a helper's in-function import and is the reason this scan exists."""
    found = _third_party_modules_the_suite_imports()
    for known in ("pytest", "numpy", "pyyaml", "paho-mqtt", "fastapi"):
        assert known in found, (known, sorted(found))


def test_the_declared_test_list_covers_every_package_the_suite_imports():
    """The general form of every dependency defect this repo has had: a package a
    collectible test needs, absent from the tier that collects it, turning a real assertion
    into an `importorskip` that reads as a pass — or, for a *helper's* in-function import,
    into a hard `ModuleNotFoundError` in the middle of a live turn.

    With the list declared once and installed by every pytest job (the two guards above),
    this closes the loop: whatever the suite imports must be IN that list, or be a named
    exception with a reason.
    """
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
    """The concrete consequence, spelled out so nobody re-derives it: DoD criterion 3 is
    proven by `test_console_roundtrip.py`, and that file opens with
    `importorskip("fastapi")`. If the test list does not carry the console's own
    requirements, the whole file reports as one green skip — which it did, on every run
    from the day it was written until PR #47."""
    gate = open(os.path.join(os.path.dirname(__file__),
                             "test_console_roundtrip.py")).read()
    assert 'importorskip("fastapi"' in gate, (
        "test_console_roundtrip.py no longer gates on fastapi — update this guard")
    declared = _requirements(HERMETIC_REQS)
    for dep in ("fastapi", "httpx"):
        assert dep in declared, (dep, sorted(declared))


def test_the_test_list_carries_protobuf_for_the_compiled_proto_oracle():
    """Specifically: the failure this whole file was written about had `protobuf` present
    (installed by hand for the QR-parity step) and no compiled protos. A tier without
    protobuf `importorskip`s past the very test that was red — so it is in the list, which
    means every tier has it rather than only the one job that named it."""
    assert "protobuf" in _requirements(HERMETIC_REQS)


def test_the_local_runner_installs_everything_ci_does():
    """`sim/tests/run.sh` provisions its venv from `sim/tests/requirements.txt` — the same
    file the fast tier's whole-suite step installs. That is now true by construction rather
    than by a comparison of two hand-written lists, so what is left to assert is that run.sh
    still installs from the file at all, and that it notices when EITHER file changes.

    The staleness half is not hypothetical in the other direction either: the stamp used to
    hash `requirements.txt` alone, and moving the packages into `requirements-hermetic.txt`
    would have left every existing venv stale while the stamp still matched — the exact
    under-provisioned venv the stamp exists to prevent.
    """
    run_sh = open(os.path.join(os.path.dirname(__file__), "run.sh")).read()
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
    """The old guard only rebuilt the venv when `pytest` was absent, so a venv holding
    pytest and nothing else was never repaired — exactly the state that produced the
    silent skips above. run.sh must key off the requirements file, not one binary."""
    run_sh = open(os.path.join(os.path.dirname(__file__), "run.sh")).read()
    assert "requirements.txt" in run_sh and "sha256sum" in run_sh, (
        "run.sh no longer re-installs when requirements.txt changes; a stale venv will "
        "under-provision the suite again")


def test_the_agent_brief_protocol_points_at_the_declared_test_list():
    """The drift escaped the repo. `docs/architecture/orchestration-plan.md` is the protocol
    every agent brief is copied from, and the venv recipe pasted out of it hand-listed
    packages — omitting `pyyaml`, `numpy` and `-r server/requirements.txt`, so agent after
    agent started with a red suite and two silently-skipped guards, for no reason and at a
    measured cost of two phantom test failures in one session.

    Only the protocol half of the plan is checked, not the append-only status log: history
    is allowed to quote what it did at the time.
    """
    plan = open(os.path.join(REPO, "docs", "architecture", "orchestration-plan.md")).read()
    protocol = plan.split("## Status log", 1)[0]
    assert "sim/tests/requirements.txt" in protocol, (
        "the orchestration plan's agent protocol does not name sim/tests/requirements.txt; "
        "a brief written from it will hand-list packages and omit one, which is exactly "
        "what happened on 2026-09-05")
    offenders = [line.strip() for line in protocol.splitlines()
                 if "pip install" in line and "pytest" in line
                 and "sim/tests/requirements" not in line]
    assert not offenders, (
        "the plan's protocol hand-lists test dependencies instead of pointing at the one "
        f"declaration: {offenders}")


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
