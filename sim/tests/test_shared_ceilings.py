"""The shared per-IP HOUR/DAY windows and the unit budget's DAY ceiling, run by pytest.

The assertions live in `helpers_shared_ceilings.mjs` (the code under test is
`functions/api/_lib/limits.js`, driven with an injected fake store — no network or
credentials). This wrapper makes `pytest sim/tests` run it and reports one failure per
section, with a floor on each section's check count so a proof cannot shrink silently.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SUITE = os.path.join(REPO, "sim", "tests", "helpers_shared_ceilings.mjs")

NODE = "node"

#: Section -> (check-count floor, what it proves), asserted both ways. F/G/H/J floors are
#: EXACT: their assertions are what `unit_budget_mutation_check.py` W5/W6/W8/D2/D11 redden.
SECTIONS = {
    "A": (10, "the fallback: with no store, admit() is the function it was before the tier"),
    "B": (10, "the per-IP HOUR binds across isolates"),
    "C": (7, "the per-IP DAY binds across isolates"),
    "D": (18, "the unit budget's DAY binds across isolates, by charge-on-completion"),
    "E": (8, "a refunded request publishes nothing to the shared day, BOTH orderings"),
    "F": (31, "the wide window fails OPEN, every failure mode by name"),
    "G": (24, "the day budget fails OPEN, every failure mode by name"),
    "H": (18, "the keys carry no address and cannot be read as each other"),
    "I": (9, "what the tier costs, as a count of round trips"),
    "J": (4, "an uncapped ceiling costs nothing at all"),
    "K": (10, "which direction each refusal errs in"),
}


@pytest.fixture(scope="module")
def result() -> dict:
    """Run the node suite ONCE. A missing `node` FAILS (a declared binary; a skip here
    would read as a pass)."""
    assert shutil.which(NODE), "node is not on PATH (see test_speech_guard.DECLARED_BINARIES)"
    proc = subprocess.run(
        [NODE, SUITE, "--json"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.stdout.strip(), (
        f"the node suite printed nothing.\nexit={proc.returncode}\nstderr:\n{proc.stderr}"
    )
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError as exc:  # pragma: no cover - a broken suite, not a branch
        raise AssertionError(
            f"the node suite did not print JSON: {exc}\nstdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        ) from exc


def test_the_suite_ran_and_has_teeth(result):
    assert result["checks"] >= 140, f"only {result['checks']} checks ran"
    assert set(result["sections"]) == set(SECTIONS), (
        "the node suite's sections and this file's list have diverged: "
        f"suite={sorted(result['sections'])} listed={sorted(SECTIONS)}"
    )


@pytest.mark.parametrize("name", sorted(SECTIONS))
def test_section(result, name):
    floor, what = SECTIONS[name]
    got = result["sections"].get(name)
    assert got is not None, f"section {name} ({what}) did not run at all"
    assert got["checks"] >= floor, (
        f"section {name} ({what}) ran {got['checks']} checks, fewer than the {floor} "
        "recorded here — assertions have been removed, which is how a proof rots silently"
    )
    mine = [f for f in result["failures"] if f.startswith(f"[{name}]")]
    assert not mine, f"section {name} ({what}) failed:\n  " + "\n  ".join(mine)


def test_no_section_failed(result):
    """A failure outside any listed section is still caught."""
    assert not result["failures"], "\n  " + "\n  ".join(result["failures"])
