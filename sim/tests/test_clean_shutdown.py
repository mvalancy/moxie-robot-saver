"""
The supervisor stops on purpose — and the broker finds out now, not in 45 seconds.

SIGTERM/SIGINT (`docker stop`, Ctrl-C) call `disconnect()`; otherwise the broker waits
1.5 × keepalive to declare the client dead, holding a ghost session and logging nothing.

One test starts a REAL supervisor and sends a REAL SIGTERM (a mocked `signal.signal`
would assert the mock), pointed at a closed port — the stop-during-reconnect-backoff
case `docker stop` actually hits. Test names are `-k` selectors in
`sim/tools/hardening_p1_mutation_check.py` — keep them.
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

#: Reserved and unbindable unprivileged, so "refused" is deterministic, not a race.
DEAD_PORT = "1"


class EchoApp(MoxieApp):
    name = "test-shutdown"

    def respond(self, turn):
        return Reply(text=f"You said: {turn.speech}")


def _rt(tmp_path, **kw):
    return make_runtime(EchoApp(), store=JsonStore(str(tmp_path)), **kw)


def _counting_disconnects(tmp_path):
    """A connected runtime whose `disconnect()` calls are counted."""
    rt, _ = _rt(tmp_path)
    rt.client.up()
    calls = []
    rt.client.disconnect = lambda: calls.append(1)
    return rt, calls


@pytest.fixture
def restore_signals():
    """Restore the process's handlers, or pytest's Ctrl-C is left replaced by a runtime's."""
    previous = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    yield
    for sig, handler in previous.items():
        signal.signal(sig, handler)


# --------------------------------------------------------------------------- #
# The stop itself
# --------------------------------------------------------------------------- #

def test_request_stop_closes_the_socket_rather_than_dropping_it(tmp_path):
    """The DISCONNECT packet is the whole difference between now and keepalive expiry."""
    rt, calls = _counting_disconnects(tmp_path)
    assert rt.request_stop(reason="SIGTERM") is True
    assert calls == [1]
    assert rt._stopping is True


def test_request_stop_is_idempotent(tmp_path):
    """A repeated or chasing signal must not start two shutdowns or write two rows."""
    rt, calls = _counting_disconnects(tmp_path)
    assert rt.request_stop() is True
    assert rt.request_stop() is False
    assert rt.request_stop() is False
    assert calls == [1]
    assert [e["kind"] for e in rt.conn_events()].count(conn.SHUTDOWN) == 1


def test_the_shutdown_row_is_written_before_the_socket_closes(tmp_path):
    """Once the socket closes, the store write races interpreter teardown — the last row
    would go missing in exactly the case an operator cares about."""
    rt, _ = _rt(tmp_path)
    rt.client.up()
    seen = []
    rt.client.disconnect = lambda: seen.append([e["kind"] for e in rt.conn_events()])

    rt.request_stop(reason="SIGTERM")
    assert seen and conn.SHUTDOWN in seen[0], \
        "the shutdown row must already be on disk when disconnect() is called"


def test_a_deliberate_stop_is_not_recorded_as_an_outage(tmp_path):
    """If every planned stop looked like an outage, the outages would mean nothing."""
    rt, _ = _rt(tmp_path)
    rt.client.up()
    rt.request_stop(reason="SIGTERM")
    rt.client.drop()                           # the disconnect the stop itself causes

    kinds = [e["kind"] for e in rt.conn_events()]
    assert kinds.count(conn.SHUTDOWN) == 1
    assert conn.DISCONNECT not in kinds
    assert conn.health(conn.summarize(rt.conn_events()), connected=False)["outages"] == 0


def test_a_stop_still_abandons_every_in_flight_turn(tmp_path):
    """A worker mid-answer at the stop must not publish into a closing socket (or, if the
    process lingers, at a child who has moved on)."""
    rt, device_id = _rt(tmp_path)
    rt.client.up()
    rt.robots["d_other"] = RobotContext(device_id="d_other", child=rt.child)
    before = {d: rt._turn_seq.get(d, 0) for d in rt.robots}

    rt.request_stop(reason="SIGTERM")
    rt.client.drop()

    for d, was in before.items():
        assert rt._turn_seq[d] > was, f"{d}'s in-flight turn was not staled by the stop"


def test_a_disconnect_that_is_not_a_stop_is_still_an_outage(tmp_path):
    """The other direction: `_stopping` must not swallow a real drop (an unconditional
    `_stopping = True` would pass the test above and erase every outage)."""
    rt, _ = _rt(tmp_path)
    rt.client.up()
    rt.client.drop()
    kinds = [e["kind"] for e in rt.conn_events()]
    assert conn.DISCONNECT in kinds and conn.SHUTDOWN not in kinds


def test_a_client_that_will_not_disconnect_does_not_stop_the_stop(tmp_path):
    """A gone broker can make `disconnect()` raise; the stop proceeds anyway."""
    rt, _ = _rt(tmp_path)
    rt.client.up()

    def boom():
        raise OSError("socket already closed")

    rt.client.disconnect = boom
    assert rt.request_stop(reason="SIGTERM") is True
    assert rt._stopping is True


def test_a_runtime_with_no_client_can_still_be_stopped(tmp_path):
    """A stop before `run()` built the client must not raise out of a signal handler."""
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
    """It cannot be caught; the store's atomic `os.replace` covers that case instead."""
    import moxie_runtime
    assert "SIGKILL" not in moxie_runtime.MoxieRuntime.STOP_SIGNALS


def test_an_embedded_runtime_installs_nothing_and_does_not_raise(tmp_path):
    """`signal.signal` works only on the main thread; an embedded runtime (SIL harness,
    a test) installs nothing — and says so, since doing nothing in the container is the
    bug."""
    rt, _ = _rt(tmp_path)
    out = {}

    def worker():
        out["installed"] = rt._install_signal_handlers()

    t = threading.Thread(target=worker)
    t.start()
    t.join(10)
    assert out.get("installed") == [], "a worker thread must not claim to have armed a stop"


def test_the_handler_starts_a_real_stop(restore_signals, tmp_path):
    rt, calls = _counting_disconnects(tmp_path)
    rt._on_stop_signal(signal.SIGTERM, None)
    assert calls == [1] and rt._stopping is True


# --------------------------------------------------------------------------- #
# The real thing: a real process, a real signal
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
def test_a_real_supervisor_exits_promptly_on_a_real_sigterm(tmp_path):
    """SIGTERM inside `loop_forever`'s reconnect backoff (closed port, no socket). The
    default disposition also "exits", so assert what only a HANDLED stop produces: the
    two log lines and `rc == 0`."""
    env = dict(os.environ)
    env.update(MOXIE_APP="echo", MOXIE_MQTT_HOST="127.0.0.1", MOXIE_MQTT_PORT=DEAD_PORT,
               MOXIE_STATUS_PORT="0", MOXIE_DATA_DIR=str(tmp_path),
               PYTHONUNBUFFERED="1",
               # creds blanked: no gateway spend to prove a signal handler
               MOXIE_LLM_API_KEY="", MOXIE_LLM_BASE_URL="",
               MOXIE_VOICE_BASE_URL="", MOXIE_STT_BASE_URL="")
    proc = subprocess.Popen([sys.executable, os.path.join(REPO, "mqtt", "run.py")],
                            cwd=REPO, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    tail = _Tail(proc)
    try:
        # generous for the BOOT, which is not the subject; `_Tail` fails fast on a death
        assert tail.wait_for("clean shutdown armed", timeout=180), \
            ("the supervisor never armed its stop signals "
             f"(alive={proc.poll() is None}):\n{tail.text()}")
        proc.send_signal(signal.SIGTERM)
        # OBSERVE the exit, don't sample it: `poll()` right after stdout EOF can be None for
        # a process that already exited 0; `wait()` blocks in `waitpid`, no race
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("SIGTERM did not stop the supervisor within 30s — a `docker stop` "
                        f"would have had to SIGKILL it:\n{tail.text()}")
        # the reader thread must still drain the pipe; the process is already gone
        assert tail.wait_closed(timeout=30), \
            f"the supervisor exited but its output never ended:\n{tail.text()}"
    finally:
        if proc.poll() is None:
            proc.kill()
    out = tail.text()
    assert proc.returncode == 0, f"a handled stop must exit 0, got {proc.returncode}\n{out}"
    assert "closing the broker connection cleanly" in out, out
    # `loop_forever` RETURNED: this line is unreachable on the default SIGTERM disposition
    assert "supervisor stopped" in out, out


class _Tail:
    """Drain a child's stdout on one thread, keeping every line; `wait_for` watches the
    collected text (a second reader on a buffered pipe loses lines)."""

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
        """True once `needle` has been printed."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if needle in self.text():
                return True
            if self._closed.wait(0.05):        # the child died without printing it
                return needle in self.text()
        return False

    def wait_closed(self, *, timeout: float) -> bool:
        return self._closed.wait(timeout)
