"""A readiness gate whose verdict is discarded is not a gate.

`sim/readiness.sh::wait_for_log` returns 1 on timeout, and call sites once threw that away,
launching the robot into the QoS-0 race the readiness line exists to prevent — then failing
20 s later as "no config pushed", which blames the appliance. Every call in `sim/*.sh` must
exit on failure or be used as a condition.
"""
from __future__ import annotations

import pathlib
import re

import pytest

SIM = pathlib.Path(__file__).resolve().parents[1]


def _call_sites():
    """(file, line, command) for each `wait_for_log` call, backslash continuations joined."""
    for sh in sorted(SIM.glob("*.sh")):
        if sh.name == "readiness.sh":
            continue
        lines = sh.read_text(encoding="utf-8").splitlines()
        i = 0
        while i < len(lines):
            joined, start = lines[i], i
            while joined.rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                joined = joined.rstrip()[:-1] + " " + lines[i].strip()
            s = joined.lstrip()
            if not s.startswith("#") and re.search(r"(^|\s)wait_for_log\s", s):
                yield sh.name, start + 1, s
            i += 1


def _is_guarded(cmd: str) -> bool:
    return bool(re.search(r"\|\|\s*(exit|return|\{)", cmd) or re.match(r"^(?:(?:if|while|until)\b|!)", cmd))


@pytest.mark.parametrize("cmd, guarded", [
    ('wait_for_log "$LOG" "ready" 40 || exit 1', True),
    ('wait_for_log "$LOG" "ready" 40 || { echo no; exit 1; }', True),
    ('if ! wait_for_log "$LOG" "ready" 40; then', True),
    ('wait_for_log "$LOG" "ready" 40', False),
    ('wait_for_log "$LOG" "ready" 40 || true', False),
])
def test_the_guard_classifier(cmd, guarded):
    assert _is_guarded(cmd) is guarded


def test_every_readiness_wait_is_acted_on():
    sites = list(_call_sites())
    assert len(sites) >= 3, sites
    assert any("status endpoint" in c for _, _, c in sites), "multi-line call not joined"
    unguarded = [f"{f}:{n}  {c}" for f, n, c in sites if not _is_guarded(c)]
    assert not unguarded, "add `|| exit 1` or use as a condition:\n  " + "\n  ".join(unguarded)
