"""Every node suite and harness mode under `sim/` is run by some CI tier — a ratchet.

A test nobody runs keeps passing on its author's machine while the code drifts (three
such files were found at once). `pytest sim/tests` collects new Python files by itself;
`sim/test_*.mjs`, `sim/run_*.sh` and each `--flag` a harness declares must be NAMED by a
tier step. The exemption lists may only shrink: a listed item that got wired in, or was
deleted, fails too.
"""
from __future__ import annotations

import glob
import os
import re

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SIM = os.path.join(REPO, "sim")
TIER_FILES = sorted(glob.glob(os.path.join(SIM, "ci", "*.yml")))

#: Files no tier runs, with a date and why. **May only shrink.**
KNOWN_UNRUN = {
    "sim/test_ambient.mjs": "2026-09-02: 56 ambient self-talk lines + face validity; "
                            "never wired into a tier.",
    "sim/test_presence_bridge.mjs": "2026-09-02: the browser SIM's vision-event bridge; "
                                    "never wired into a tier.",
    "sim/run_acl_proof.sh": "2026-09-02: the broker ACL proof (18 checks against real "
                            "mosquitto); needs docker, belongs in the deep tier.",
}
#: Harness invocations no tier runs. **May only shrink.**
KNOWN_UNRUN_MODES = {
    "sim/run_smoke.sh --telehealth": "2026-09-02: the telehealth SIL round-trip through a "
                                     "real broker (caught the double-END_SESSION bug).",
}


def _tier_text() -> str:
    return "\n".join(open(p).read() for p in TIER_FILES)


def _test_files() -> set:
    return {os.path.relpath(p, REPO) for pat in ("test_*.mjs", "run_*.sh")
            for p in glob.glob(os.path.join(SIM, pat))}


def unrun(text: str | None = None) -> set:
    body = _tier_text() if text is None else text
    return _test_files() - set(re.findall(r"sim/(?:test_\w+\.mjs|run_\w+\.sh)", body))


def _declared_flags() -> set:
    """`sim/run_*.sh --flag` for every flag a script's own `case` arms accept. The `(`
    lookbehind skips `UP+=(--build)`, an argument a script PASSES."""
    return {f"{os.path.relpath(p, REPO)} --{flag}"
            for p in glob.glob(os.path.join(SIM, "run_*.sh"))
            for flag in re.findall(r"(?<![\w(])--([a-z][a-z0-9-]*)\)", open(p).read())}


def unrun_modes() -> set:
    text = _tier_text()
    return {inv for inv in _declared_flags() if inv not in text}


def test_the_scanners_see_real_inputs_and_would_notice_a_gap():
    assert {"ci.yml", "ci-deep.yml"} <= {os.path.basename(p) for p in TIER_FILES}
    assert len(_test_files()) >= 15
    assert unrun("jobs: {}\n") == _test_files(), "the matcher must report unnamed files"
    flags = _declared_flags()
    assert "sim/run_smoke.sh --telehealth" in flags
    assert not any(f.endswith("--build") for f in flags), flags


def test_every_sim_test_file_is_run_by_some_tier():
    missing = sorted(unrun() - set(KNOWN_UNRUN))
    assert not missing, (f"no CI tier runs {missing}: add a step to sim/ci/ci.yml (or "
                         "ci-deep.yml) and .github/workflows/, or a DATED KNOWN_UNRUN entry")


def test_every_declared_harness_mode_is_run_by_some_tier():
    missing = sorted(unrun_modes() - set(KNOWN_UNRUN_MODES))
    assert not missing, f"no tier runs {missing}; wire it or add a dated KNOWN_UNRUN_MODES entry"


@pytest.mark.parametrize("item", sorted({**KNOWN_UNRUN, **KNOWN_UNRUN_MODES}))
def test_an_exemption_is_still_needed(item):
    """The reverse direction — how the original gap would have survived a one-way list."""
    if item in KNOWN_UNRUN:
        assert os.path.exists(os.path.join(REPO, item)), f"{item} is gone; drop its entry"
        assert item in unrun(), f"{item} IS now run by a tier; drop its KNOWN_UNRUN entry"
    else:
        assert item in _declared_flags(), f"{item} is no longer declared; drop its entry"
        assert item in unrun_modes(), f"{item} IS now run by a tier; drop its entry"
    reason = {**KNOWN_UNRUN, **KNOWN_UNRUN_MODES}[item]
    assert re.match(r"^\d{4}-\d{2}-\d{2}: ", reason), "an exemption must be dated"
