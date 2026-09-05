"""
👋 The supervisor stops on purpose — and the broker finds out now, not in 45 seconds.

Build document:
[`docs/architecture/backlog/production-hardening.md`](../../docs/architecture/backlog/production-hardening.md)
**§8 P1** — *"a SIGTERM handler that calls `disconnect()` so the broker logs a clean close
instead of a 45 s keepalive timeout (which is also what makes `_device_disconnect`'s regex
fire promptly)."*

Why 45 seconds is the number. §4.1's C2 keeps the keepalive at **30 s** deliberately, and
MQTT gives the broker 1.5 × keepalive before it declares a client dead. So a supervisor
killed with its TCP session open is, from mosquitto's point of view, still connected for
three-quarters of a minute. Two things follow, and the second is the one that bites:

* the broker holds a session for `client_id="supervisor"` that no longer exists — and a
  supervisor that comes back inside that window is talking past its own ghost;
* `$SYS/broker/log` emits **nothing** until the timeout, so `DISCONNECT_RE` never fires.
  The appliance's own record of the stop is a silence.

`docker stop`, `docker compose restart`, `systemctl stop` and Ctrl-C all send SIGTERM or
SIGINT — i.e. every ordinary way this process ever ends, apart from a crash.

Hermetic. One test starts a **real supervisor subprocess** and sends it a **real SIGTERM**,
because the thing under test is a signal handler and a `signal.signal` call that works in
one thread and not another; a mock of it would assert the mock. It needs no broker: it
points the supervisor at a closed port, which also proves the case `docker stop` actually
hits — a stop *during* the reconnect ladder, where the client has no socket to close and
`loop_forever` is inside its backoff.
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
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
sys.path.insert(0, os.path.dirname(__file__))

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
    """Put the process's own handlers back.

    `_install_signal_handlers` really calls `signal.signal`, so a test that installed one
    and walked away would leave pytest's SIGINT handling replaced by a runtime that has
    been garbage collected — and the failure would surface in some *other* test, as a
    Ctrl-C that does nothing.
    """
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
    """The one test that cannot be faked, and the case `docker stop` actually hits.

    The supervisor is pointed at a **closed** port, so it is inside `loop_forever`'s
    reconnect backoff with no socket to close — which is where a naive handler is most
    likely to leave the process wedged, because there is no connection for `disconnect()`
    to tear down and paho has to notice the state change on its own. Before this slice
    there was no handler at all: the default SIGTERM disposition killed the process, which
    *looks* the same from outside and is exactly why an assertion on "it exited" alone
    would prove nothing. So the assertions are on what only a handled stop can produce —
    the log lines and `rc == 0`.
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
        # Generous on purpose, and the generosity is about the *boot*, not about the
        # subject. Booting a supervisor is not what this test asserts; a slow boot on a
        # loaded runner making it red would be the test measuring the machine. (Seen once,
        # at 40 s, with 16 CPU burners and a soak running — the process was alive and
        # still importing.) The assertions that matter — rc == 0 and the two log lines —
        # are unaffected by how long the boot took, and `_Tail` fails fast if the child
        # dies instead of printing. Playbook rule 11: assert recorded state, and do not
        # let a live timing sample decide the verdict.
        assert tail.wait_for("clean shutdown armed", timeout=180), \
            ("the supervisor never armed its stop signals "
             f"(alive={proc.poll() is None}):\n{tail.text()}")
        proc.send_signal(signal.SIGTERM)
        # OBSERVE THE EXIT; DO NOT SAMPLE IT. This used to read
        #
        #     if not tail.wait_closed(timeout=30) or proc.poll() is None: fail(...)
        #
        # and the second half is a race, not a check. `wait_closed` returns the instant
        # the child's stdout reaches EOF — which happens while the kernel is still tearing
        # the process down — so `poll()` on the very next line can legitimately answer
        # `None` for a process that has already stopped. Measured on 2026-09-04 with the
        # box oversubscribed 40 CPU burners deep: `poll()` was None at EOF in 3 of 6 runs,
        # every one of which had already printed both shutdown lines and exited 0. The
        # message that fell out of it — "SIGTERM did not stop the supervisor within 30s" —
        # was reached in under a tenth of a second and was simply untrue, which is exactly
        # how a gate teaches people to re-run it instead of reading it.
        #
        # `proc.wait(timeout=30)` is the same 30-second bound (NOT widened) spent on the
        # thing actually being claimed: it blocks in `waitpid` until the child is really
        # gone, so there is no window to be scheduled into.
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
    """THE TEETH for the line above, and the whole reason it changed.

    The test above used to decide "the supervisor did not stop" from two facts read a few
    microseconds apart: the child's stdout had reached EOF, and `poll()` had not yet seen
    an exit status. Those are not contradictory — a process closes its file descriptors
    on the way out and is reapable slightly later — so the pair proves nothing, and on a
    loaded runner it produced a confident thirty-second verdict in about a tenth of a
    second.

    This builds that window on purpose and with **no wall clock in it at all**: the child
    closes fd 1 itself and then blocks forever on stdin, so it is unambiguously alive with
    its output stream unambiguously ended. If `poll()` at EOF were sound, this test could
    not exist.

    Then it shows the replacement doing the right thing on the same process: close stdin,
    and `wait()` — which blocks in `waitpid` rather than sampling it — reports the true
    exit.
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
    """Drain a child's stdout on a thread, keeping every line.

    The first draft had the reader thread `return` as soon as it found the line it was
    waiting for, and then read the rest with `communicate()`. That works and is subtly
    racy: `for line in proc.stdout` reads through a buffer, so the lines already pulled
    into it when the thread returned were simply lost — including, on an unlucky
    scheduling, the two lines the assertions are about. One reader, one buffer, nothing
    handed over: `wait_for` watches what has been collected instead of consuming it.
    """

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
