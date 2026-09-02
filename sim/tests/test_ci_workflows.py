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

import os

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
