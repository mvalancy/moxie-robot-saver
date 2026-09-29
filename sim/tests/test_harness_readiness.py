"""A SIL script must WAIT for the stack, never guess at it.

A blind `sleep` is wrong both ways: the broker listened after 0.35 s while the script slept
5, and on a loaded runner an 8 s boot became "no config pushed within timeout" — a boot
failure reported as a verdict on the robot. Every `sim/*.sh` that boots `mqtt/run.py` waits
on an observable line. Not named `test_sil_*`: both CI tiers deselect that prefix early.
"""
import os
import re
import sys
from contextlib import redirect_stdout

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SIM = os.path.join(REPO, "sim")

#: The SUBACK line, not the CONNACK one: a robot announcing before the SUBACK has its
#: QoS-0, non-retained config push deleted.
READY_LINE = "[runtime] subscriptions acknowledged by the broker"


def _code(name: str) -> str:
    """A script with comment-only lines dropped (they document history, sleeps included)."""
    with open(os.path.join(SIM, name), encoding="utf-8") as fh:
        return "\n".join(ln for ln in fh.read().splitlines() if not ln.lstrip().startswith("#"))


SCRIPTS = sorted(n for n in os.listdir(SIM) if n.endswith(".sh"))
BOOTERS = [n for n in SCRIPTS if "mqtt/run.py" in _code(n)]


def test_every_supervisor_booting_script_waits_for_the_readiness_line_not_a_sleep():
    """`run_broker_outage.sh` boots with NO broker on purpose; its `sleep 6` is the
    duration under test. Sub-second poll cadences are fine."""
    assert len(BOOTERS) >= 3, BOOTERS
    allowed = {"run_broker_outage.sh": {"6"}, "run_compose_smoke.sh": {"2"}}
    for name in BOOTERS + [n for n in allowed if n in SCRIPTS]:
        code = _code(name)
        if name != "run_broker_outage.sh" and name in BOOTERS:
            assert READY_LINE in code, f"{name} boots mqtt/run.py without wait_for_log"
        sleeps = [n for n in re.findall(r"^\s*sleep\s+(\d+(?:\.\d+)?)\s*$", code, re.M)
                  if float(n) >= 1 and n not in allowed.get(name, set())]
        assert not sleeps, f"{name}: `sleep {sleeps}` guesses a boot; wait on a condition"


def test_the_readiness_helpers_live_in_one_place():
    helpers = _code("readiness.sh")
    assert "wait_for_port(){" in helpers and "wait_for_log(){" in helpers
    definers = [n for n in SCRIPTS if n != "readiness.sh"
                and re.search(r"^\s*wait_for_(port|log)\(\)\s*\{", _code(n), re.M)]
    assert not definers, f"{definers} define their own wait_for_* instead of sourcing it"
    for name in ("run_smoke.sh", "run_scenarios.sh"):
        assert re.search(r"^\s*\.\s+sim/readiness\.sh\s*$", _code(name), re.M), name


def _runtime():
    sys.path[:0] = [os.path.join(REPO, "mqtt"), os.path.join(REPO, "mqtt", "supervisor")]
    pytest.importorskip("paho.mqtt.client")
    import moxie_runtime
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.types import ChildProfile

    class _App(MoxieApp):
        name = "echo"

    return moxie_runtime.MoxieRuntime(app=_App(), child=ChildProfile(nickname="Sam"))


class _FlushRecordingIO:
    def __init__(self):
        self.events = []

    def write(self, s):
        if s.strip():
            self.events.append(("write", s))
        return len(s)

    def flush(self):
        self.events.append(("flush", None))


def test_the_runtime_prints_the_readiness_line_and_flushes_it():
    """Waiters read a redirected stdout, where Python block-buffers: an unflushed signal
    is a supervisor that looks hung for 40 s after connecting in 0.11 s."""
    rt = _runtime()

    class _Client:
        def subscribe(self, topic, qos=0):
            return (0, 1)

    out = _FlushRecordingIO()
    with redirect_stdout(out):
        rt._on_connect(_Client(), None, {}, 0)
        rt._on_subscribe(None, None, 1, [0], None)
    idx = next((i for i, (k, p) in enumerate(out.events) if k == "write" and READY_LINE in p),
               None)
    assert idx is not None, f"the readiness line was never written: {out.events}"
    assert ("flush", None) in out.events[idx:], "print(..., flush=True)"


def test_the_telehealth_arm_checks_the_status_bind_the_runtime_reports():
    """`--telehealth` drives the supervisor's status HTTP; a stale process on the port once
    made the robot POST into a stranger. The script must wait for the bind line, check the
    failure line, and let an operator choose the port — and the runtime must print both."""
    from helpers_runtime import runtime_source
    src, smoke = runtime_source(), _code("run_smoke.sh")
    assert "[runtime] status endpoint on http://127.0.0.1:" in src
    assert "[runtime] status server failed" in src
    assert "status server failed" in smoke
    assert "[runtime] status endpoint on http://127.0.0.1:$STATUS_PORT/status" in smoke
    for name in ("run_smoke.sh", "run_scenarios.sh"):
        assert "MOXIE_STATUS_PORT:-" in _code(name), f"{name} ignores MOXIE_STATUS_PORT"


def test_the_status_rows_telemetry_count_is_a_length_and_never_none():
    """A SIL wait once gated on `telemetry_count is not None`, which could never be false."""
    from moxie_sdk.types import RobotContext
    rt = _runtime()
    device = "d_00000000-0000-4000-8000-00000000feed"
    rt.robots[device] = RobotContext(device_id=device)
    row = next(r for r in rt.status_snapshot()["robots"] if r["device_id"] == device)
    assert row["telemetry_count"] == 0


@pytest.mark.parametrize("name", ("run_scenarios.sh", "run_smoke.sh"))
def test_teardown_waits_for_children_and_cannot_fail_a_passing_run(name):
    """A passing run once exited 1 on `rm: Directory not empty`: `kill` only requests an
    exit (the SIGTERM handler still writes), and under `bash -e` the failing `rm` aborted
    cleanup. Signal, confirm gone (bounded), then `rm -rf ... || true`."""
    src = open(os.path.join(SIM, name)).read()
    body = src[src.index("cleanup()"):src.index("trap cleanup EXIT")]
    assert "kill -0" in body and re.search(r"seq 1 \d+", body), body
    assert "|| true" in next(ln for ln in body.splitlines() if "rm -rf" in ln)
