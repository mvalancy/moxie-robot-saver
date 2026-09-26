"""The shared per-IP HOUR/DAY windows and the unit budget's DAY ceiling, run by pytest.

A wrapper: every assertion lives in `sim/tests/helpers_shared_ceilings.mjs`, because the
code under test (`functions/api/_lib/limits.js`) is JavaScript and a Cache API tier is only
honestly tested by driving the real module with a real injected store. This file makes
`pytest sim/tests` run that suite (a new Python file cannot go silently unrun, unlike a
`sim/test_*.mjs` that a CI tier must name) and reports one failure per SECTION:

  A  the fallback — with no store, `admit()` is the function it was before the tier
  B  the per-IP HOUR really binds across isolates
  C  the per-IP DAY really binds across isolates
  D  the unit budget's DAY, by charge-on-completion (no refund write to lose)
  E  a refunded request publishes NOTHING to the shared day
  F  the wide window fails OPEN — every failure mode by name
  G  the day budget fails OPEN — every failure mode by name, including a `put` that lands
     and then hangs
  H  the keys: no address, no route in the budget key, and the mark that separates a wide
     entry from a narrow one
  I  what it costs, as a count of round trips
  J  an uncapped ceiling costs nothing at all
  K  which direction each refusal errs in, including the inherited overcount it does NOT fix

No credentials, network or browser: the suite injects a fake store and never calls
`fetch`; its gateway key `sk-testonly-…` exists only inside that file.
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

#: Every section the node suite must report, with a floor on its check count. Listed here
#: rather than discovered, and asserted from BOTH sides below: a section that vanishes is a
#: failure, and a section the suite grew that nobody listed is a failure too. A green
#: number that quietly got smaller is exactly how a proof rots.
#:
#: F, G, H and J are pinned to their EXACT counts deliberately: specific assertions there
#: are the only thing that reddens for `unit_budget_mutation_check.py` rows W5, W6, W8, D2
#: and D11, and deleting one would unhook a row's proof while it kept saying "caught".
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
    """Run the node suite ONCE and hand every test the same recorded outcome.

    A missing `node` is a hard failure rather than a skip. This repo's recorded trap is a
    missing dependency making the tests that need it skip themselves away — a skip that
    reads as a pass — and `node` is declared in `test_speech_guard.py`'s
    `DECLARED_BINARIES` precisely because the fast CI tier already runs ~20 node suites.
    """
    assert shutil.which(NODE), (
        "node is not on PATH. It is a declared dependency of this suite "
        "(sim/tests/test_speech_guard.py::DECLARED_BINARIES) and the fast CI tier already "
        "runs ~20 `node sim/test_*.mjs` steps, so this is a broken environment, not a "
        "reason to skip the only proof that the shared hour/day ceilings bind."
    )
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
    """A wrapper whose subject printed zero checks passes vacuously. Pin both ends."""
    assert result["checks"] >= 140, f"only {result['checks']} checks ran"
    assert set(result["sections"]) == set(SECTIONS), (
        "the node suite's sections and this file's list have diverged: "
        f"suite={sorted(result['sections'])} listed={sorted(SECTIONS)}"
    )


@pytest.mark.parametrize("name", sorted(SECTIONS))
def test_section(result, name):
    """One pytest failure per section, naming the assertions that reddened."""
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
    """The whole-suite view, so a failure outside any listed section is still caught."""
    assert not result["failures"], "\n  " + "\n  ".join(result["failures"])
