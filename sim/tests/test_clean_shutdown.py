"""
👋 The supervisor stops on purpose — and the broker finds out now, not in 45 seconds.

`production-hardening.md` §8 P1: SIGTERM/SIGINT (`docker stop`, `compose restart`,
`systemctl stop`, Ctrl-C) call `disconnect()`. With keepalive 30 s the broker waits
1.5 × keepalive = 45 s to declare a killed client dead, holding a ghost session for
`client_id="supervisor"` and emitting nothing on `$SYS/broker/log` (so `DISCONNECT_RE`
never fires).

One test starts a REAL supervisor subprocess and sends a REAL SIGTERM (a mocked
`signal.signal` would assert the mock), pointed at a closed port — the stop-during-
reconnect-backoff case `docker stop` actually hits, with no socket to close.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

from helpers_runtime import make_runtime                          # noqa: E402
from moxie_sdk import conn_telemetry as conn                      # noqa: E402
from moxie_sdk.app import MoxieApp                                # noqa: E402
from moxie_sdk.store import JsonStore                             # noqa: E402
from moxie_sdk.types import Reply, RobotContext                   # noqa: E402

#: A port nothing listens on, so `connect_async` fails instantly and forever. Port 1 is
#: reserved and unbindable by an unprivileged process, which is what makes "refused" the
#: deterministic answer rather than a race with whatever else the machine is running.
DEAD_PORT = "1"


class EchoApp(MoxieApp):
    name = "test-shutdown"

    def respond(self, turn):
        return Reply(text=f"You said: {turn.speech}")


def _rt(tmp_path, **kw):
    return make_runtime(EchoApp(), store=JsonStore(str(tmp_path)), **kw)


@pytest.fixture
def restore_signals():
    """Restore the process's own signal handlers, so pytest's SIGINT handling is not left
    replaced by a collected runtime (surfacing later as a Ctrl-C that does nothing)."""
    previous = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    yield
    for sig, handler in previous.items():
        signal.signal(sig, handler)


# --------------------------------------------------------------------------- #
# The stop itself
# --------------------------------------------------------------------------- #

def test_request_stop_closes_the_socket_rather_than_dropping_it(tmp_path):
    """`disconnect()` sends a DISCONNECT packet. That is the entire difference between the
    broker knowing now and the broker knowing at the keepalive expiry."""
    rt, _ = _rt(tmp_path)
    rt.client.up()
    calls = []
    rt.client.disconnect = lambda: calls.append(1)

    assert rt.request_stop(reason="SIGTERM") is True
    assert calls == [1]
    assert rt._stopping is True


def test_request_stop_is_idempotent(tmp_path):
    """A container runtime that sends SIGTERM and then SIGTERM again, or a SIGINT chasing
    a SIGTERM, must not start two shutdowns — and must not write two `shutdown` rows."""
    rt, _ = _rt(tmp_path)
    rt.client.up()
    calls = []
    rt.client.disconnect = lambda: calls.append(1)

    assert rt.request_stop() is True
    assert rt.request_stop() is False
    assert rt.request_stop() is False
    assert calls == [1]
    assert [e["kind"] for e in rt.conn_events()].count(conn.SHUTDOWN) == 1


def test_the_shutdown_row_is_written_before_the_socket_closes(tmp_path):
    """Ordering, and it is deliberate. Once the socket is closing the store write is racing
    the interpreter's teardown — so a history whose last row is missing would be missing it
    in exactly the case an operator cares about."""
    rt, _ = _rt(tmp_path)
    rt.client.up()
    seen = []
    rt.client.disconnect = lambda: seen.append([e["kind"] for e in rt.conn_events()])

    rt.request_stop(reason="SIGTERM")
    assert seen and conn.SHUTDOWN in seen[0], \
        "the shutdown row must already be on disk when disconnect() is called"


def test_a_deliberate_stop_is_not_recorded_as_an_outage(tmp_path):
    """An operator reading a history where every planned stop looks like an outage learns
    nothing from the outages. One `shutdown`, no `disconnect`."""
    rt, _ = _rt(tmp_path)
    rt.client.up()
    rt.request_stop(reason="SIGTERM")
    rt.client.drop()                           # the disconnect the stop itself causes

    kinds = [e["kind"] for e in rt.conn_events()]
    assert kinds.count(conn.SHUTDOWN) == 1
    assert conn.DISCONNECT not in kinds
    assert conn.health(conn.summarize(rt.conn_events()), connected=False)["outages"] == 0


def test_a_stop_still_abandons_every_in_flight_turn(tmp_path):
    """The clean path must not quietly re-open §4.2. A worker that was mid-answer when the
    stop arrived would otherwise publish into a socket that is closing — and if the process
    survives long enough, at a child who has moved on."""
    rt, device_id = _rt(tmp_path)
    rt.client.up()
    rt.robots["d_other"] = RobotContext(device_id="d_other", child=rt.child)
    before = {d: rt._turn_seq.get(d, 0) for d in rt.robots}

    rt.request_stop(reason="SIGTERM")
    rt.client.drop()

    for d, was in before.items():
        assert rt._turn_seq[d] > was, f"{d}'s in-flight turn was not staled by the stop"


def test_a_disconnect_that_is_not_a_stop_is_still_an_outage(tmp_path):
    """The other direction of the same guard: `_stopping` must not swallow a real drop.
    (Without this pair, setting `_stopping = True` unconditionally would pass the test
    above and silently erase every outage the appliance ever has.)"""
    rt, _ = _rt(tmp_path)
    rt.client.up()
    rt.client.drop()
    kinds = [e["kind"] for e in rt.conn_events()]
    assert conn.DISCONNECT in kinds and conn.SHUTDOWN not in kinds


def test_a_client_that_will_not_disconnect_does_not_stop_the_stop(tmp_path):
    """A broker that has already gone away can make `disconnect()` raise. The appliance is
    on its way out; refusing to leave because the goodbye failed is not an improvement."""
    rt, _ = _rt(tmp_path)
    rt.client.up()

    def boom():
        raise OSError("socket already closed")

    rt.client.disconnect = boom
    assert rt.request_stop(reason="SIGTERM") is True
    assert rt._stopping is True


def test_a_runtime_with_no_client_can_still_be_stopped(tmp_path):
    """`run()` builds the client; a stop before that (a crash-loop in compose, a fast
    Ctrl-C) must not raise out of a signal handler."""
    rt, _ = _rt(tmp_path)
    rt.client = None
    assert rt.request_stop(reason="SIGINT") is True


# --------------------------------------------------------------------------- #
# Arming the handlers
# --------------------------------------------------------------------------- #

def test_both_stop_signals_are_installed_on_the_main_thread(restore_signals, tmp_path):
    rt, _ = _rt(tmp_path)
    installed = rt._install_signal_handlers()
    assert set(installed) == {"SIGTERM", "SIGINT"}
    for name in installed:
        assert signal.getsignal(getattr(signal, name)) == rt._on_stop_signal


def test_sigkill_is_deliberately_not_in_the_list():
    """It cannot be caught. The case it stands for is covered by the store's atomic
    `os.replace` instead, which is why §5.3's A6 kills the writer twenty times."""
    import moxie_runtime
    assert "SIGKILL" not in moxie_runtime.MoxieRuntime.STOP_SIGNALS


def test_an_embedded_runtime_installs_nothing_and_does_not_raise(tmp_path):
    """`signal.signal` only works on the main thread of the main interpreter, and the
    runtime is legitimately embedded — the SIL harness, a test, a supervisor-in-a-thread.
    Silently doing nothing *there* is right; silently doing nothing in the container is the
    bug, which is why the method says out loud which it did."""
    rt, _ = _rt(tmp_path)
    out = {}

    def worker():
        out["installed"] = rt._install_signal_handlers()

    t = threading.Thread(target=worker)
    t.start()
    t.join(10)
    assert out.get("installed") == [], "a worker thread must not claim to have armed a stop"


def test_the_handler_starts_a_real_stop(restore_signals, tmp_path):
    rt, _ = _rt(tmp_path)
    rt.client.up()
    calls = []
    rt.client.disconnect = lambda: calls.append(1)
    rt._on_stop_signal(signal.SIGTERM, None)
    assert calls == [1] and rt._stopping is True


# --------------------------------------------------------------------------- #
# The real thing: a real process, a real signal
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
def test_a_real_supervisor_exits_promptly_on_a_real_sigterm(tmp_path):
    """The un-fakeable case: SIGTERM while inside `loop_forever`'s reconnect backoff (closed
    port, no socket). The default disposition also "exits", so the assertions are on what
    only a HANDLED stop produces — the two log lines and `rc == 0`.
    """
    env = dict(os.environ)
    env.update(MOXIE_APP="echo", MOXIE_MQTT_HOST="127.0.0.1", MOXIE_MQTT_PORT=DEAD_PORT,
               MOXIE_STATUS_PORT="0", MOXIE_DATA_DIR=str(tmp_path),
               PYTHONUNBUFFERED="1",
               # Creds blanked: a supervisor that reached a gateway from a unit test would
               # be spending money to prove a signal handler works.
               MOXIE_LLM_API_KEY="", MOXIE_LLM_BASE_URL="",
               MOXIE_VOICE_BASE_URL="", MOXIE_STT_BASE_URL="")
    proc = subprocess.Popen([sys.executable, os.path.join(REPO, "mqtt", "run.py")],
                            cwd=REPO, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    tail = _Tail(proc)
    try:
        # Generous for the BOOT, which is not the subject (seen at 40 s on a saturated box).
        # rc == 0 and the log lines don't depend on boot time, and `_Tail` fails fast if the
        # child dies instead of printing.
        assert tail.wait_for("clean shutdown armed", timeout=180), \
            ("the supervisor never armed its stop signals "
             f"(alive={proc.poll() is None}):\n{tail.text()}")
        proc.send_signal(signal.SIGTERM)
        # OBSERVE the exit, don't sample it: stdout EOF arrives while the kernel is still
        # tearing the process down, so `poll()` right after can be None for a process that
        # already exited 0. `proc.wait(timeout=30)` blocks in `waitpid` — same bound, no race.
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("SIGTERM did not stop the supervisor within 30s — a `docker stop` "
                        f"would have had to SIGKILL it:\n{tail.text()}")
        # The reader thread still has to run out the pipe before the log is complete; the
        # process is already gone, so this can only be a scheduling wait.
        assert tail.wait_closed(timeout=30), \
            f"the supervisor exited but its output never ended:\n{tail.text()}"
    finally:
        if proc.poll() is None:
            proc.kill()
    out = tail.text()
    assert proc.returncode == 0, f"a handled stop must exit 0, got {proc.returncode}\n{out}"
    assert "closing the broker connection cleanly" in out, out
    # `loop_forever` **returned** rather than the process being torn down under it — the
    # line after it is the proof, and it is unreachable on the default SIGTERM disposition,
    # which is what the process had before this slice.
    assert "supervisor stopped" in out, out


@pytest.mark.skipif(os.name != "posix", reason="fd surgery on the child's stdout")
def test_a_closed_stdout_is_not_proof_that_the_process_has_exited():
    """Teeth for the line above: a child that closes fd 1 and then blocks on stdin is alive
    with its output ended — so "EOF + `poll() is None`" proves nothing. Then closing stdin
    and `wait()` reports the true exit. No wall clock involved.
    """
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import os, sys; sys.stdout.write('bye\\n'); sys.stdout.flush(); "
         "os.close(1); sys.stdin.readline()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True)
    try:
        tail = _Tail(child)
        assert tail.wait_closed(timeout=30), "the child never closed its stdout"
        assert child.poll() is None, (
            "this test needs a process that is alive with a closed stdout; if that is no "
            "longer constructible, the sampled idiom may be safe again — but prove it "
            "here rather than by assuming it")
        assert "bye" in tail.text(), tail.text()
        child.stdin.close()                       # the only thing keeping it alive
        assert child.wait(timeout=30) == 0, "the observed exit disagreed with the child"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


class _Tail:
    """Drain a child's stdout on one thread, keeping every line; `wait_for` watches what was
    collected rather than consuming it (handing a buffered pipe to a second reader loses
    lines)."""

    def __init__(self, proc):
        self._lines: list = []
        self._closed = threading.Event()
        self._proc = proc
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self):
        try:
            for line in self._proc.stdout:
                self._lines.append(line)
        finally:
            self._closed.set()

    def text(self) -> str:
        return "".join(self._lines)

    def wait_for(self, needle: str, *, timeout: float) -> bool:
        """True once `needle` has been printed. Polls the collected text rather than the
        pipe, so it cannot consume anything a later assertion needs."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in self.text():
                return True
            if self._closed.wait(0.05):        # the child died without printing it
                return needle in self.text()
        return False

    def wait_closed(self, *, timeout: float) -> bool:
        return self._closed.wait(timeout)
