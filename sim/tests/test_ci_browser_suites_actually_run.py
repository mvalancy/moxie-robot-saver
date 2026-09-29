"""The browser suites must actually RUN in CI.

With no workflow installing puppeteer, nine suites printed "skipped — puppeteer not found"
on every run and the job stayed GREEN (including the peak-amplitude and mic-spend
guards). Checked PER JOB: an install in `sil` does nothing for a suite in `browser`, and
the original workflow did install a browser — ~40 lines after every browser step.
"""
import os
import re
from pathlib import Path

import pytest

from test_ci_test_coverage import KNOWN_UNRUN

yaml = pytest.importorskip("yaml", reason="the workflow guards parse YAML")

ROOT = Path(__file__).resolve().parents[2]
CI_YML = ROOT / "sim" / "ci" / "ci.yml"
INSTALL_RE = re.compile(r"\bnpm (?:install|i)\b[^\n]*\bpuppeteer\b")
#: A step that DISPATCHES a suite, not prose naming one.
DISPATCH_RE = re.compile(r"^\s*node\s+(sim/test_\w+\.mjs)", re.M)


def _browser_suites():
    return sorted(p.name for p in (ROOT / "sim").glob("test_*.mjs")
                  if re.search(r"loadPuppeteer|requireBrowser", p.read_text(encoding="utf-8")))


def _jobs():
    return yaml.safe_load(CI_YML.read_text(encoding="utf-8"))["jobs"]


def _install_index(job):
    return next((i for i, s in enumerate(job.get("steps") or [])
                 if INSTALL_RE.search(s.get("run") or "")), None)


def test_every_browser_suite_is_dispatched_after_its_jobs_browser_install():
    suites = _browser_suites()
    assert len(suites) >= 10, suites
    dispatched, offenders = set(), []
    for job_id, job in _jobs().items():
        install = _install_index(job)
        for i, step in enumerate(job.get("steps") or []):
            for script in DISPATCH_RE.findall(step.get("run") or ""):
                name = os.path.basename(script)
                dispatched.add(name)
                if name in suites and (install is None or install > i):
                    offenders.append(f"{name} (job {job_id} step #{i + 1}, install #{install})")
    assert not offenders, f"dispatched before (or without) a browser install: {offenders}"
    exempt = {os.path.basename(p) for p in KNOWN_UNRUN}
    assert not sorted(set(suites) - dispatched - exempt), "wire it into the `browser` job"


def test_the_job_that_runs_the_browser_suites_checks_the_repo_out_and_has_python():
    """Suites spawn `python3 sim/serve.py` and load docs.html, which reads the bundle."""
    for job_id, job in _jobs().items():
        if _install_index(job) is None:
            continue
        steps = job.get("steps") or []
        uses = [s.get("uses", "") for s in steps]
        assert any(u.startswith("actions/checkout") for u in uses), job_id
        assert any(u.startswith("actions/setup-python") for u in uses), job_id
        assert "build_docs_bundle.py" in "\n".join(s.get("run") or "" for s in steps), job_id


def test_a_missing_browser_is_a_failure_under_ci():
    """Every skip path must be CI-aware: four suites once had their OWN silent `skip()`."""
    harness = (ROOT / "sim" / "browser_harness.mjs").read_text(encoding="utf-8")
    assert "process.env.CI" in harness and "process.exit(1)" in harness
    offenders = [n for n in _browser_suites()
                 if re.search(r"function skip\s*\(", src := (ROOT / "sim" / n).read_text())
                 and "process.env.CI" not in src and "skipper(" not in src]
    assert not offenders, offenders
