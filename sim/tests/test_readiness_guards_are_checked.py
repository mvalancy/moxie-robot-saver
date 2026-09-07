"""🚦 A readiness gate whose verdict is discarded is not a gate.

**The finding (2026-09-07).** `sim/readiness.sh::wait_for_log` is careful: on timeout it
prints `❌ supervisor never logged '<needle>' in Ns`, dumps 20 lines of the supervisor log,
and **returns 1**. Two of its three call sites threw that away:

    sim/run_smoke.sh:238     ... || exit 1        # the status endpoint — checked
    sim/run_smoke.sh:223     ...                  # the SUBACK        — NOT checked
    sim/run_scenarios.sh:75  ...                  # the SUBACK        — NOT checked

So a supervisor that never acknowledged its subscriptions produced an accurate error
message and then **the script launched the robot anyway** — into precisely the QoS-0
race the readiness line exists to prevent (`test_sil_supervisor_readiness.py` explains
that race at length). Twenty seconds later the run failed as `no config pushed within
timeout`, which reads as *the appliance did not answer* when what actually happened is
*we proceeded past a gate that told us not to*.

**Why a ratchet rather than a process test.** The race itself already has a test that
reproduces it on the wire by holding the SUBSCRIBE packet. This is the different half:
not "is the gate correct" but "is the gate *obeyed*". That is a property of the call
sites, so it is checked where it lives — cheaply, hermetically, and in a way that fails
the moment someone adds a fourth unguarded caller.

**What this file proves.** Every `wait_for_log` invocation in `sim/*.sh` either exits on
failure or is used as a condition. It does not assert *how many* call sites there are: new
waits are welcome, unguarded ones are not.
"""
from __future__ import annotations

import pathlib
import re

SIM = pathlib.Path(__file__).resolve().parents[1]

#: The definition itself, and the doc-comment above it, are not call sites.
DEFINITION = "sim/readiness.sh"


def _call_sites():
    """Every `wait_for_log ...` invocation in a sim shell script, with its full command.

    A call may be wrapped across lines with a trailing backslash, so continuations are
    joined before the guard is looked for — otherwise a perfectly guarded two-line call
    would read as unguarded, which is the same class of mistake this file is about.
    """
    for sh in sorted(SIM.glob("*.sh")):
        rel = f"sim/{sh.name}"
        if rel == DEFINITION:
            continue
        lines = sh.read_text(encoding="utf-8").splitlines()
        i = 0
        while i < len(lines):
            joined, start = lines[i], i
            while joined.rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                joined = joined.rstrip()[:-1] + " " + lines[i].strip()
            stripped = joined.lstrip()
            if stripped.startswith("wait_for_log ") or " wait_for_log " in f" {stripped}":
                if not stripped.startswith("#"):
                    yield rel, start + 1, joined
            i += 1


def _is_guarded(cmd: str) -> bool:
    """True when this invocation's failure actually stops the run.

    Three honest shapes: `|| exit`, `|| { ...; exit ...; }`, and use as a condition
    (`if ! wait_for_log ...`), which hands the decision to the branch.
    """
    s = cmd.strip()
    if re.search(r"\|\|\s*(exit|return|\{)", s):
        return True
    if re.match(r"^(if|while|until)\b", s) or s.lstrip().startswith("!"):
        return True
    return False


def test_every_readiness_wait_is_acted_on():
    sites = list(_call_sites())
    assert sites, "no wait_for_log call sites found — the scanner is broken, not the scripts"
    unguarded = [(f, n, c.strip()) for f, n, c in sites if not _is_guarded(c)]
    assert not unguarded, (
        "these readiness waits report a failure and then let the run continue:\n  "
        + "\n  ".join(f"{f}:{n}  {c}" for f, n, c in unguarded)
        + "\n\nAdd `|| exit 1`, or use the call as a condition. `wait_for_log` already "
          "prints the error and the log tail; what is missing is stopping. A run that "
          "proceeds past its own readiness gate fails later, somewhere else, for a reason "
          "that blames the appliance."
    )


def test_the_scanner_sees_a_guarded_multiline_call():
    """The status-endpoint wait in run_smoke.sh is guarded ACROSS A LINE BREAK.

    It is the reason `_call_sites` joins continuations. Pinning it here means a future
    simplification of the scanner cannot quietly start reporting a correct call as a
    violation — which would train its reader to ignore the failure, the same disease.
    """
    joined = [c for f, n, c in _call_sites() if "status endpoint" in c]
    assert len(joined) == 1, f"expected exactly one status-endpoint wait, got {len(joined)}"
    assert _is_guarded(joined[0]), joined[0]
