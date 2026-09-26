"""A SIL script must WAIT for the stack, never guess at it.

A blind `sleep` is wrong both ways: measured with a docker broker, the broker listened after
0.35 s and the supervisor was ready after 0.11 s (the script slept 2 + 3), while an 8 s slow
`mqtt/run.py` (a loaded runner) produced "no config pushed within timeout" — a boot failure
reported as a false accusation against the subject under test.

This guards the CLASS: any `sim/*.sh` that boots `mqtt/run.py` must wait on an observable
condition (shaped like `test_roster.py::test_every_sil_script_that_boots_a_supervisor_
scopes_its_own_data_dir`).

Pure file reading, so deliberately NOT named `test_sil_*` — both CI tiers deselect
`-k "not test_sil and not test_docs"`, which would make this guard a test that never runs.
"""
import os
import re

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SIM = os.path.join(REPO, "sim")

#: The readiness line every supervisor-booting script waits for, kept literal so a runtime
#: rewording goes red here. It is the SUBACK line from `_on_subscribe`, not
#: `[runtime] broker connected`: `subscribe()` only queues a packet, and a robot announcing
#: before the SUBACK has its QoS-0, non-retained config push deleted (not delayed).
READY_LINE = "[runtime] subscriptions acknowledged by the broker"

#: The CONNACK line. Still printed, still true, still what `/status`'s `broker_connected`
#: and the console's connection card mean — it is simply not a licence to put a robot on
#: the bus. Kept here so the guards below can prove BOTH exist and in which order.
CONNECT_LINE = "[runtime] broker connected"


def _scripts():
    for name in sorted(os.listdir(SIM)):
        if name.endswith(".sh"):
            yield name, open(os.path.join(SIM, name), encoding="utf-8").read()


def _code(src: str) -> str:
    """The script with comment-only lines dropped — so a `sleep` *described* in a comment
    (this repo documents its history in comments) cannot trip a guard about behaviour."""
    return "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))


def _boots_supervisor(code: str) -> bool:
    return "mqtt/run.py" in code


def test_every_supervisor_booting_script_waits_for_the_readiness_line():
    offenders = [name for name, src in _scripts()
                 if _boots_supervisor(_code(src)) and READY_LINE not in _code(src)]
    # run_broker_outage.sh boots a supervisor with NO broker on purpose and asserts it
    # stays up; there is no readiness line to wait for, which is the point of the test.
    offenders = [n for n in offenders if n != "run_broker_outage.sh"]
    assert not offenders, (
        f"{offenders} boot mqtt/run.py without waiting for {READY_LINE!r}. A fixed sleep "
        f"is wrong in both directions: it wastes seconds on a warm box and, on a loaded "
        f"one, turns a boot that had not finished into a 20 s 'no config pushed within "
        f"timeout' against the robot. Source sim/readiness.sh and call wait_for_log.")


def test_no_supervisor_booting_script_guesses_the_boot_with_a_bare_sleep():
    """A `sleep N` (N >= 1) in a boot path is the anti-pattern. Sub-second poll cadences stay,
    as does `run_broker_outage.sh`'s `sleep 6` — the duration UNDER TEST ("still alive after
    6 s with nothing listening")."""
    allowed = {"run_broker_outage.sh": {"6"}, "run_compose_smoke.sh": {"2"}}
    offenders = []
    for name, src in _scripts():
        code = _code(src)
        if not _boots_supervisor(code) and name not in allowed:
            continue
        for n in re.findall(r"^\s*sleep\s+(\d+(?:\.\d+)?)\s*$", code, re.M):
            if float(n) >= 1 and n not in allowed.get(name, set()):
                offenders.append(f"{name}: sleep {n}")
    assert not offenders, (
        f"{offenders} — a whole-second sleep in a boot path is a guess. The conditions are "
        f"observable: wait_for_port for the broker, wait_for_log for the supervisor.")


def test_the_readiness_helpers_live_in_one_place():
    """Two copies of a wait are two waits: `run_scenarios.sh` and `run_smoke.sh` both source
    `sim/readiness.sh`."""
    helpers = os.path.join(SIM, "readiness.sh")
    assert os.path.isfile(helpers), "sim/readiness.sh is gone"
    text = open(helpers, encoding="utf-8").read()
    assert "wait_for_port(){" in text and "wait_for_log(){" in text

    definers = [name for name, src in _scripts()
                if name != "readiness.sh"
                and re.search(r"^\s*wait_for_(port|log)\(\)\s*\{", _code(src), re.M)]
    assert not definers, (
        f"{definers} define their own wait_for_* instead of sourcing sim/readiness.sh")

    for name in ("run_smoke.sh", "run_scenarios.sh"):
        code = _code(open(os.path.join(SIM, name), encoding="utf-8").read())
        assert re.search(r"^\s*\.\s+sim/readiness\.sh\s*$", code, re.M), \
            f"{name} does not source sim/readiness.sh"


def test_the_readiness_line_is_the_one_the_runtime_actually_prints():
    """The needle must still exist in the runtime, or every script waits 40 s for a line
    nobody prints — a boot failure disguised as a slow boot."""
    from helpers_runtime import runtime_source
    src = runtime_source()
    # The needle, not the whole call — the call also carries `flush=True`, which the
    # behavioural test at the bottom of this file owns.
    assert '"[runtime] subscriptions acknowledged by the broker ' in src, (
        "the runtime no longer prints the readiness line the SIL scripts wait for; "
        "update READY_LINE here and in sim/readiness.sh together")
    # And the CONNACK line survives with it: `/status`, the console card and
    # `test_connection_resilience.py`'s rc=5 guard all still mean that one, so a fix that
    # renamed it instead of adding beside it would have moved the ground under them.
    assert '"[runtime] broker connected rc=' in src, (
        "the CONNACK line is gone. It was not the SUBACK signal's to remove — the "
        "readiness fix ADDS a second, later line precisely so this one keeps its meaning")


# --------------------------------------------------------------------------- #
# The other half of the readiness contract: printed last AND actually observable.
# --------------------------------------------------------------------------- #
# `test_connect_readiness.py` makes the line TRUE; this makes it VISIBLE. Consumers redirect
# stdout to a file, where Python block-buffers, so the line must be printed with
# `flush=True` — `PYTHONUNBUFFERED=1` in callers is belt, the keyword is braces.
class _FlushRecordingIO:
    """Enough of a text stream for `print`, recording the order of writes and flushes."""

    def __init__(self):
        self.events = []

    def write(self, s):
        if s.strip():
            self.events.append(("write", s))
        return len(s)

    def flush(self):
        self.events.append(("flush", None))


def test_the_readiness_line_is_flushed_when_it_is_printed():
    """Order of effects, not source text: the readiness write must be followed by a flush
    before `_on_connect` returns, whatever buffering the caller happens to have set up."""
    import sys as _sys
    from contextlib import redirect_stdout

    _sys.path.insert(0, os.path.join(REPO, "mqtt"))
    _sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
    import pytest
    pytest.importorskip("paho.mqtt.client")
    import moxie_runtime
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.types import ChildProfile

    class _App(MoxieApp):
        name = "echo"

    rt = moxie_runtime.MoxieRuntime(app=_App(), child=ChildProfile(nickname="Sam"))

    class _Client:
        def subscribe(self, topic, qos=0):
            return (0, 1)                   # (rc, mid), as paho returns

    out = _FlushRecordingIO()
    with redirect_stdout(out):
        rt._on_connect(_Client(), None, {}, 0)
        # …and the SUBACK the broker answers it with, which is what the scripts wait for.
        rt._on_subscribe(None, None, 1, [0], None)

    idx = next((i for i, (kind, payload) in enumerate(out.events)
                if kind == "write" and READY_LINE in payload), None)
    assert idx is not None, f"the readiness line was never written: {out.events}"
    assert any(kind == "flush" for kind, _ in out.events[idx:]), (
        f"{READY_LINE!r} was written but never flushed. Every waiter reads it from a "
        f"redirected stdout, where Python block-buffers, so an unflushed readiness signal "
        f"is a supervisor that looks hung for 40 s after connecting in 0.11 s. "
        f"print(..., flush=True) — the refusal branch beside it already does.")


# --------------------------------------------------------------------------- #
# The same class again, one port along: a precondition nobody looked at.
# --------------------------------------------------------------------------- #
# `run_smoke.sh --telehealth` drives the robot over the supervisor's status HTTP, so that
# bind is load-bearing: a stale process holding the port made the robot POST into a stranger
# and fail as a misleading JSON error. `_start_status_server` prints both outcomes; the
# script must check them.
def _smoke() -> str:
    return open(os.path.join(SIM, "run_smoke.sh"), encoding="utf-8").read()


def test_the_telehealth_arm_checks_the_status_endpoint_it_is_about_to_drive():
    code = _code(_smoke())
    assert "status server failed" in code, (
        "run_smoke.sh --telehealth drives the supervisor's status HTTP but never checks "
        "that the bind succeeded; on a taken port it silently drives whatever else is "
        "listening there")
    assert "[runtime] status endpoint on http://127.0.0.1:$STATUS_PORT/status" in code, (
        "the positive signal is not waited for — the runtime prints that line only after "
        "HTTPServer(...) has bound, so it is the honest one to wait on")


def test_the_operator_can_choose_the_status_port_in_both_scripts():
    """A derived port can collide too, and until 2026-09-03 `run_smoke.sh` offered no
    lever when it did — it overwrote `MOXIE_STATUS_PORT` unconditionally, while
    `run_scenarios.sh` had always honoured it."""
    for name in ("run_smoke.sh", "run_scenarios.sh"):
        code = _code(open(os.path.join(SIM, name), encoding="utf-8").read())
        assert re.search(r"MOXIE_STATUS_PORT:-", code), (
            f"{name} ignores an operator's MOXIE_STATUS_PORT")


def test_the_runtime_still_prints_both_status_bind_outcomes():
    """The two needles above are only worth anything while the runtime prints them —
    and it must print BOTH, because a bind that fails silently is the original bug."""
    from helpers_runtime import runtime_source
    src = runtime_source()
    assert '"[runtime] status endpoint on http://127.0.0.1:{port}/status"' in src \
        or '[runtime] status endpoint on http://127.0.0.1:' in src, \
        "the success line moved; update run_smoke.sh's wait with it"
    assert "[runtime] status server failed" in src, \
        "the failure line moved; run_smoke.sh's --telehealth guard greps for it"


# --------------------------------------------------------------------------- #
# The fourth location of the same shape: a wait that could not fail.
# --------------------------------------------------------------------------- #
# A SIL wait once used `row.get("telemetry_count") is not None` as "hydrated", but the field
# is `len(self._telemetry_buffer(...))` — never None — so the clause could not be false.
# This pins that fact, so making the field nullable goes red next to the test relying on it.
def test_the_status_rows_telemetry_count_is_a_length_and_never_none():
    import sys as _sys

    _sys.path.insert(0, os.path.join(REPO, "mqtt"))
    _sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
    import pytest
    pytest.importorskip("paho.mqtt.client")
    import moxie_runtime
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.types import ChildProfile, RobotContext

    class _App(MoxieApp):
        name = "echo"

    rt = moxie_runtime.MoxieRuntime(app=_App(), child=ChildProfile(nickname="Sam"))
    device = "d_00000000-0000-4000-8000-00000000feed"
    rt.robots[device] = RobotContext(device_id=device)

    row = next(r for r in rt.status_snapshot()["robots"] if r["device_id"] == device)
    assert row["telemetry_count"] == 0, row
    assert row["telemetry_count"] is not None, (
        "a robot with no telemetry at all still reports a number, not None — so a wait "
        "predicate gated on `telemetry_count is not None` is vacuous and waits for "
        "nothing. Gate on the value you mean, or assert it with a named reason.")


# ---------------------------------------------------------------------------
# TEARDOWN MUST NOT RACE, AND MUST NOT FAIL A PASSING RUN
#
# A passing run once exited 1 on `rm: cannot remove …: Directory not empty`. Two defects:
#   1. `kill` only REQUESTS an exit; the SIGTERM handler flushes state, so `rm -rf` could
#      race a dying writer. Wait for the processes to be gone first.
#   2. Under `bash -e` the failing `rm` aborted cleanup before `return 0`.
# ---------------------------------------------------------------------------
def test_sil_scripts_wait_for_their_children_before_deleting_the_data_dir():
    """Signal, confirm gone, then remove — and never let teardown fail the run."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for name in ("run_scenarios.sh", "run_smoke.sh"):
        src = (root / "sim" / name).read_text()
        body = src[src.index("cleanup()"):]
        body = body[:body.index("trap cleanup EXIT")]

        assert "kill -0" in body, (
            f"{name}: cleanup deletes MOXIE_DATA_DIR without confirming its children "
            f"are gone. `kill` only requests an exit; a supervisor with a SIGTERM "
            f"handler keeps writing while `rm -rf` walks the tree."
        )
        # the wait must be bounded, or a wedged child hangs the CI job for ever
        assert re.search(r"seq 1 \d+", body), (
            f"{name}: the wait for children must be bounded — an unbounded loop "
            f"trades a flaky red for a hung job."
        )
        rm_line = next((l for l in body.splitlines() if "rm -rf" in l), "")
        assert "|| true" in rm_line, (
            f"{name}: `rm -rf` in cleanup must not be able to fail the run — under "
            f"`bash -e` it aborts the function before `return 0`, so a teardown "
            f"problem is reported as a test failure. Got: {rm_line.strip()!r}"
        )
