"""Every test file in `sim/` is run by some CI tier — a ratchet, not a wish.

`live-sim-demo.md` §10 records the finding this exists for: `sim/test_ambient.mjs` and
`sim/test_presence_bridge.mjs` are executed by **no** tier. Both pass locally today, so
nothing is broken — but two green tests nobody runs are not evidence, and the failure
mode is silent in the worst possible way: the file keeps passing on the author's machine
for months while the code it guards drifts.

Nothing in the repo could notice that, because the tiers are hand-written YAML step
lists (`sim/ci/*.yml`; pushing under `.github/workflows/` needs a token scope this
project does not have, so the templates are the version-controlled source of truth and
`test_ci_workflows.py` holds the installed copies byte-identical to them). A test file is
"run" only if some step's command names it. So: enumerate the files, enumerate the
references, and compare.

**It is a ratchet, deliberately.** Wiring the two offenders in needs an edit to
`sim/ci/ci.yml`, which this pass was not allowed to touch, so a hard assertion would just
redden the tier. `KNOWN_UNRUN` names exactly the files nobody runs, with the date and the
reason, and it is asserted from **both** sides:

* a file that is neither referenced nor listed → **fail** (the gap can never reappear
  silently in a new test);
* a listed file that has become referenced, or has been deleted → **fail** (the list can
  only ever shrink, so nobody inherits a stale exemption).

That second direction is the whole design. An allowlist checked in one direction is how
the original gap would have survived this guard too.
"""
from __future__ import annotations

import glob
import os
import re

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SIM = os.path.join(REPO, "sim")
CI_DIR = os.path.join(SIM, "ci")

#: The tier definitions, as templates. `.github/workflows/` holds installed copies that
#: `test_ci_workflows.py` proves byte-identical; reading the templates keeps this guard
#: meaningful in a clone that has no `.github/` at all.
TIER_FILES = sorted(glob.glob(os.path.join(CI_DIR, "*.yml")))

#: Files no tier runs, with why. **This list may only shrink.**
KNOWN_UNRUN = {
    "sim/test_ambient.mjs":
        "2026-09-02: never wired into a tier (found while specing live-Sim P0-a; "
        "56 ambient self-talk lines + face validity — passes locally, run by nobody). "
        "Wiring it needs sim/ci/ci.yml, which the integration pass that found it was "
        "not allowed to edit.",
    "sim/test_presence_bridge.mjs":
        "2026-09-02: same fire — the browser SIM's vision-event bridge (6 events, the "
        "greeting record, badge + toggle). Passes locally, run by nobody.",
    "sim/test_ambient_guard.mjs":
        "2026-09-04: written UNWIRED on purpose. It guards the fix for ambient self-talk "
        "cutting off a live gateway answer (sim/web/ambient.js + audio.js). A concurrent "
        "pass is rewriting sim/ci/ci.yml, .github/workflows/ci.yml, sim/browser_harness.mjs "
        "and all nine existing browser suites — it is auditing them for hidden dependence "
        "on local machine state, after the finding that nine suites had never actually run "
        "in CI. Editing the tier here would collide with that work and fight the "
        "template/installed parity guard. 29 checks, verified green four consecutive runs "
        "locally: PUPPETEER_PATH=… node sim/test_ambient_guard.mjs. Wire it into the deep "
        "tier (it needs a browser, ~90 s) as soon as that pass lands.",
    "sim/run_acl_proof.sh":
        "2026-09-02 (found by this guard): the broker ACL proof from PR #44 — 18 checks "
        "against a real eclipse-mosquitto:2.0.20, the only thing that holds the pattern "
        "ACLs honest — is run by no tier either. It needs docker, so it belongs in the "
        "deep tier next to run_compose_smoke.sh.",
}


def _tier_text() -> str:
    parts = []
    for p in TIER_FILES:
        with open(p) as fh:
            parts.append(fh.read())
    return "\n".join(parts)


def _test_files() -> list:
    """Every executable test artifact under `sim/` that a CI step has to name, repo-relative.

    Two families, because both have been silently unrun: the node `.mjs` suites and the
    `run_*.sh` harnesses. Python under `sim/tests/` is deliberately excluded — `pytest
    sim/tests` collects a new file with no wiring at all, which is exactly why that family
    has never had this problem.
    """
    out = [os.path.relpath(p, REPO)
           for p in glob.glob(os.path.join(SIM, "test_*.mjs"))]
    out += [os.path.relpath(p, REPO)
            for p in glob.glob(os.path.join(SIM, "run_*.sh"))]
    return sorted(out)


def _referenced(text: str | None = None) -> set:
    """The set of `sim/…` test artifacts some tier step actually names."""
    body = _tier_text() if text is None else text
    return set(re.findall(r"sim/(?:test_[A-Za-z0-9_]+\.mjs|run_[A-Za-z0-9_]+\.sh)", body))


def unrun(text: str | None = None) -> set:
    """Test artifacts that exist on disk and are named by no tier."""
    return set(_test_files()) - _referenced(text)


# --------------------------------------------------------------- the guard is real --
def test_the_tier_definitions_were_actually_found():
    """A guard whose inputs are empty passes vacuously. Pin both ends."""
    assert TIER_FILES, f"no tier YAML under {CI_DIR}"
    names = {os.path.basename(p) for p in TIER_FILES}
    assert {"ci.yml", "ci-deep.yml"} <= names, names
    files = _test_files()
    assert len(files) >= 15, f"only {len(files)} test artifacts found under sim/: {files}"
    assert len(_referenced()) >= 12, sorted(_referenced())


def test_the_guard_notices_a_file_no_tier_names():
    """Teeth: run the same comparison against a tier text that names nothing, and every
    file on disk must come back unrun. Without this, a green ratchet proves nothing about
    whether the matcher works."""
    assert unrun("name: nothing\njobs: {}\n") == set(_test_files())


# ------------------------------------------------------------------- the ratchet --
def test_every_sim_test_file_is_run_by_some_tier():
    """The forward direction: a new `.mjs` or `run_*.sh` that nobody wired in fails here."""
    missing = sorted(unrun() - set(KNOWN_UNRUN))
    assert not missing, (
        "these test files exist under sim/ but no CI tier runs them:\n  "
        + "\n  ".join(missing)
        + "\n\nAdd a step naming each one to sim/ci/ci.yml (fast) or sim/ci/ci-deep.yml "
          "(needs docker/a browser/minutes), and keep .github/workflows/ in parity. If "
          "it genuinely cannot be wired yet, add it to KNOWN_UNRUN in this file WITH a "
          "date and a reason — that list is read by the next audit.")


@pytest.mark.parametrize("path", sorted(KNOWN_UNRUN))
def test_a_known_unrun_file_still_exists(path):
    """The list may only shrink. A deleted file must leave it."""
    assert os.path.exists(os.path.join(REPO, path)), (
        f"{path} is in KNOWN_UNRUN but no longer exists — delete its entry")


@pytest.mark.parametrize("path", sorted(KNOWN_UNRUN))
def test_a_known_unrun_file_that_got_wired_in_leaves_the_list(path):
    """The reverse direction, which is the point: the moment somebody adds the step, this
    fails and the exemption has to go. An allowlist checked only forwards is how the
    original gap would have survived this guard too."""
    assert path in unrun(), (
        f"{path} IS now run by a tier — remove it from KNOWN_UNRUN "
        f"(reason on record: {KNOWN_UNRUN[path]})")


def test_the_known_unrun_reasons_are_dated():
    """An exemption with no date is an exemption nobody will ever revisit."""
    for path, why in {**KNOWN_UNRUN, **KNOWN_UNRUN_MODES}.items():
        assert re.match(r"^\d{4}-\d{2}-\d{2}", why), f"{path}: reason must start with a date"
        assert len(why) > 60, f"{path}: say what the file covers and why it is not wired"


# ------------------------------------------------------- the same gap, per MODE --
#
# A referenced *file* is not a covered *harness*. `sim/run_smoke.sh` is named by two
# tiers, but only in its default mode — its `--telehealth` arm (the whole
# enable→start→speak→interrupt→end chain PR #43 shipped, and the only end-to-end proof
# 🎭 puppet mode has) is run by nobody. The flags are declared in the scripts' own `case`
# arms, so they can be enumerated rather than listed by hand: a new flag must be wired
# into a tier or exempted here.

#: Harness invocations no tier runs. **May only shrink**, same as `KNOWN_UNRUN`.
KNOWN_UNRUN_MODES = {
    "sim/run_smoke.sh --telehealth":
        "2026-09-02 (found by this guard): the telehealth SIL round-trip — operator "
        "enable → START_SESSION → PLAY_OUTPUT → INTERRUPT → END_SESSION through a real "
        "broker, which is where the double-END_SESSION bug was caught — runs only when "
        "somebody types it. Needs one step in sim/ci/ci.yml next to the plain smoke.",
}


def _declared_flags() -> set:
    """`sim/run_*.sh --flag` for every flag a script's own `case` arms declare.

    The negative lookbehind on `(` is load-bearing: `run_compose_smoke.sh` builds an
    argv with `UP+=(--build)`, which is an argument it *passes*, not a flag it accepts.
    """
    out = set()
    for p in sorted(glob.glob(os.path.join(SIM, "run_*.sh"))):
        with open(p) as fh:
            body = fh.read()
        rel = os.path.relpath(p, REPO)
        for flag in re.findall(r"(?<![\w(])--([a-z][a-z0-9-]*)\)", body):
            out.add(f"{rel} --{flag}")
    return out


def unrun_modes() -> set:
    text = _tier_text()
    return {inv for inv in _declared_flags() if inv not in text}


def test_the_flag_scan_finds_the_flags_and_not_the_arguments():
    flags = _declared_flags()
    assert "sim/run_smoke.sh --telehealth" in flags, flags
    assert not any(f.endswith("--build") for f in flags), \
        f"`UP+=(--build)` is an argument run_compose_smoke.sh passes, not a flag: {flags}"


def test_every_declared_harness_mode_is_run_by_some_tier():
    missing = sorted(unrun_modes() - set(KNOWN_UNRUN_MODES))
    assert not missing, (
        "these harness modes are declared by a sim/run_*.sh script but no tier runs "
        "them:\n  " + "\n  ".join(missing) +
        "\n\nA referenced script is not a covered mode — add the invocation to a tier, "
        "or exempt it in KNOWN_UNRUN_MODES with a date and a reason.")


@pytest.mark.parametrize("invocation", sorted(KNOWN_UNRUN_MODES))
def test_a_known_unrun_mode_that_got_wired_in_leaves_the_list(invocation):
    assert invocation in unrun_modes(), (
        f"{invocation} IS now run by a tier — remove it from KNOWN_UNRUN_MODES")


@pytest.mark.parametrize("invocation", sorted(KNOWN_UNRUN_MODES))
def test_a_known_unrun_mode_is_still_declared(invocation):
    assert invocation in _declared_flags(), (
        f"{invocation} is exempted but no sim/run_*.sh declares that flag any more")
