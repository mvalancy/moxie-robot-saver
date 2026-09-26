"""The browser suites must actually RUN in CI.

`sim/browser_harness.mjs::loadPuppeteer` falls back to a developer-machine path, and its
`skipper()` exits 0. With no workflow installing puppeteer, nine suites printed "skipped —
puppeteer not found" on every run and the job stayed GREEN — including
`test_typed_turn.mjs` (a real peak-amplitude assertion so a silent clip cannot pass) and
`test_mic_spend.mjs` (a refused microphone must not spend the gateway). These tests are
the ratchet.

PER-JOB, because the Chrome suites run in their own `browser` job (on `sil` they caused
load contention that reddened unrelated tests). With two jobs a file-wide ordering check
passes meaninglessly — `sil`'s install "precedes" `browser`'s dispatches in byte order
while doing nothing for them — so every check resolves a dispatch to its own job.
"""
import os
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="the workflow guards parse YAML")

try:  # the one exemption list, read from both guards rather than restated in each
    from test_ci_test_coverage import KNOWN_UNRUN
except ImportError:  # pytest run with `--import-mode=importlib`, or this file alone
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "_ci_test_coverage", os.path.join(os.path.dirname(__file__), "test_ci_test_coverage.py"))
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    KNOWN_UNRUN = _mod.KNOWN_UNRUN

ROOT = Path(__file__).resolve().parents[2]
CI_YML = ROOT / "sim" / "ci" / "ci.yml"
HARNESS = ROOT / "sim" / "browser_harness.mjs"

#: `run:` line that installs puppeteer.
INSTALL_RE = re.compile(r"\bnpm (?:install|i)\b[^\n]*\bpuppeteer\b")
#: A step that DISPATCHES a suite (`node sim/test_x.mjs`) — not a mention of one. The
#: explanatory comments around these steps name suites in prose, and an `index()` on the
#: bare filename finds the prose instead of the step. That false positive is exactly the
#: kind of thing that makes a guard useless, so the pattern is anchored to how a step runs.
DISPATCH_RE = re.compile(r"^\s*node\s+(sim/test_[A-Za-z0-9_]+\.mjs)", re.M)


def _browser_suites():
    """Suite files that need a real browser, found by what they import — not by a hand list."""
    out = []
    for p in sorted((ROOT / "sim").glob("test_*.mjs")):
        src = p.read_text(encoding="utf-8")
        if "loadPuppeteer" in src or "requireBrowser" in src:
            out.append(p.name)
    return out


def _jobs():
    return yaml.safe_load(CI_YML.read_text(encoding="utf-8"))["jobs"]


def _dispatch_map():
    """`{suite filename: [(job_id, step_index), …]}` over the whole fast tier."""
    out = {}
    for job_id, job in _jobs().items():
        for i, step in enumerate(job.get("steps") or []):
            for script in DISPATCH_RE.findall(step.get("run") or ""):
                out.setdefault(os.path.basename(script), []).append((job_id, i))
    return out


def _install_index(job):
    """The step index of the puppeteer install in this job, or None."""
    for i, step in enumerate(job.get("steps") or []):
        if INSTALL_RE.search(step.get("run") or ""):
            return i
    return None


# ------------------------------------------------------------------ the inputs are real --
def test_the_scan_finds_both_the_suites_and_the_workflow():
    """A guard whose inputs are empty passes vacuously. Pin both ends."""
    suites = _browser_suites()
    assert suites, "found no browser suites — has the harness API been renamed?"
    assert len(suites) >= 10, suites
    dispatched = _dispatch_map()
    assert dispatched, "sim/ci/ci.yml dispatches no node suite at all"
    assert any(_install_index(j) is not None for j in _jobs().values()), (
        "sim/ci/ci.yml installs no puppeteer anywhere. Without it every browser suite "
        f"skips and the tier stays green while {len(suites)} suites assert nothing: "
        + ", ".join(suites))


# --------------------------------------------------------- every suite is actually wired --
def test_every_browser_suite_is_dispatched_by_the_fast_tier():
    """A suite that exists and runs nowhere is the original bug in miniature. Exemptions
    are `test_ci_test_coverage.KNOWN_UNRUN`, imported rather than restated, so one list
    governs both guards and may only shrink."""
    dispatched = set(_dispatch_map())
    exempt = {os.path.basename(p) for p in KNOWN_UNRUN}
    missing = sorted(set(_browser_suites()) - dispatched - exempt)
    assert not missing, (
        "these suites import the browser harness but no fast-tier job runs them:\n  "
        + "\n  ".join(missing)
        + "\n\nAdd a `run: node sim/<file>` step to the `browser` job in sim/ci/ci.yml "
          "(and keep .github/workflows/ci.yml in parity), or — if it genuinely cannot be "
          "wired yet — add it to KNOWN_UNRUN in test_ci_test_coverage.py with a date and "
          "a reason.")


# ------------------------------------------- the install is in the SAME job, and before --
def test_ci_installs_a_browser_before_it_runs_any_browser_suite():
    """An install step must come BEFORE the first browser suite **of its own job**: the
    original workflow DID install a browser, ~40 lines after every browser step had
    skipped, and an install in `sil` does nothing for a suite in `browser`."""
    jobs = _jobs()
    offenders = []
    for suite, sites in sorted(_dispatch_map().items()):
        if suite not in _browser_suites():
            continue
        for job_id, at in sites:
            install = _install_index(jobs[job_id])
            if install is None:
                offenders.append(
                    f"{suite}: dispatched by job `{job_id}` (step #{at + 1}), which never "
                    f"installs puppeteer — it would skip, or FAIL under CI")
            elif install > at:
                offenders.append(
                    f"{suite}: job `{job_id}` installs the browser at step #{install + 1}, "
                    f"after dispatching it at step #{at + 1}")
    assert not offenders, (
        "the browser is not installed before the suites that need it:\n  "
        + "\n  ".join(offenders)
        + "\n\nThat is the exact shape of the original defect: a real install step, in the "
          "wrong place, reading as coverage.")


def test_the_job_that_runs_the_browser_suites_checks_the_repo_out_and_has_python():
    """The suites are not self-contained node: four of them `spawn("python3",
    ["sim/serve.py", …])` and load `docs.html`, which reads the generated docs bundle. A
    job that installs a browser and nothing else would fail in a way that looks like a
    broken runner rather than a missing step."""
    for job_id, job in _jobs().items():
        if _install_index(job) is None:
            continue
        steps = job.get("steps") or []
        uses = [s.get("uses", "") for s in steps]
        runs = "\n".join(s.get("run") or "" for s in steps)
        assert any(u.startswith("actions/checkout") for u in uses), job_id
        assert any(u.startswith("actions/setup-python") for u in uses), (
            f"job `{job_id}` runs browser suites that spawn `python3 sim/serve.py` but "
            f"pins no python")
        assert "build_docs_bundle.py" in runs, (
            f"job `{job_id}` loads docs.html (test_docs_explorer / test_mermaid / test_csp "
            f"/ test_responsive) but never builds the bundle docs.html reads")


# ------------------------------------------------------- a missing browser must be loud --
def test_a_missing_browser_is_a_failure_under_ci_and_a_clean_skip_locally():
    """Every skip path must be CI-aware; a skip nobody can see is not a skip.

    Checked at the source level across every suite, because four suites had their OWN local
    `skip()` and a fix applied only to the shared harness would have left them silently green.
    """
    harness = HARNESS.read_text(encoding="utf-8")
    assert "process.env.CI" in harness and "process.exit(1)" in harness, (
        "browser_harness.mjs::skipper must exit non-zero under CI, or a broken install "
        "silently deletes coverage again")

    offenders = []
    for name in _browser_suites():
        src = (ROOT / "sim" / name).read_text(encoding="utf-8")
        # a suite may define its own skip, but it must be CI-aware — either by delegating to
        # the shared skipper, or by checking CI itself.
        defines_own = re.search(r"function skip\s*\(", src)
        if defines_own and "process.env.CI" not in src and "skipper(" not in src:
            offenders.append(name)
    assert not offenders, (
        "these suites define their own skip() that ignores CI, so they exit 0 on a runner "
        f"with no browser: {', '.join(offenders)}")
