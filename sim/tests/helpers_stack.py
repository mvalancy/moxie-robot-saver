"""
Boot the REAL stack — broker + `mqtt/run.py` supervisor — from a test, on free ports.

`sim/run_smoke.sh` already does this for the shell; a pytest that wants to prove
something about the *assembled appliance* (which voice `config.build_synthesizer()`
actually picked, what the supervisor logged at startup, what a real robot heard back)
had no way to say so without re-implementing the boot each time.

Everything here picks its own free port (never 1883/8930 — a lab machine has stale
supervisors and sibling agents on those), keeps its data in a caller-supplied scratch
dir, and tears down only the processes it started. Nothing kills a process it did not
create; a leftover broker on the default port is stepped around, never over.

Used by `test_live_gateway_turn_e2e.py`. It is deliberately NOT imported by the hermetic
tier: booting a broker is seconds, not milliseconds, and hermetic tests get the
in-process loopback in `helpers_runtime.loopback()` instead.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BROKER_CONF = os.path.join(REPO, "sim", "broker", "ci-mosquitto.conf")

#: THE SUPERVISOR'S TWO READINESS LINES, AND WHY THERE ARE TWO.
#:
#: `CONNECT_LINE` is the CONNACK: a socket exists, the broker said yes, and the runtime
#: has CALLED `subscribe()`. That call only queues a SUBSCRIBE packet — under
#: `loop_forever()` the bytes leave on the network thread after the callback returns — so
#: the line means *"we asked"*. Anything that then puts a robot on the bus is racing: a
#: `/state` published in that window reaches a broker holding no matching subscription,
#: and the config push that answers a `/state` is QoS 0 and not retained, so it is never
#: generated and never replayed. HIL, 2026-09-05:
#: `❌ scenario 'basic-conversation': 0/4 turns OK — no config pushed within timeout`,
#: with the *second* scenario green in the same job.
#:
#: `SUBSCRIBED_LINE` is the SUBACK, printed from the runtime's `_on_subscribe`, and it is
#: the only one a robot may be booted on. `Supervisor.start()` waits for it by default;
#: `start(ready_line=CONNECT_LINE)` exists so a test can deliberately stand in the gap.
CONNECT_LINE = "[runtime] broker connected"
SUBSCRIBED_LINE = "[runtime] subscriptions acknowledged by the broker"


def free_port() -> int:
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def broker_available() -> bool:
    """A mosquitto we can actually start: the binary, or docker with an image we can run."""
    if shutil.which("mosquitto"):
        return True
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


class Broker:
    """An anonymous mosquitto on a free port, exactly the one CI/SIL uses.

    Binary first (that is what CI installs); docker `eclipse-mosquitto:2` otherwise. The
    docker path mounts the conf UNCHANGED and publishes `host:free → container:1883` —
    rewriting the listener inside the container while mapping to 1883 is the trap
    `run_smoke.sh` avoids by mounting the original file, and so do we.
    """

    def __init__(self, log_dir: str):
        self.port = free_port()
        self.log = os.path.join(log_dir, "broker.log")
        self._proc = None
        self._cid = ""

    def start(self, timeout: float = 15.0) -> "Broker":
        if shutil.which("mosquitto"):
            conf = os.path.join(os.path.dirname(self.log), "mosquitto.conf")
            with open(BROKER_CONF) as fh:
                text = fh.read()
            # the binary listens directly on the host port; the websocket listener is
            # dropped so two concurrent runs cannot collide on 9001
            text = text.replace("listener 1883", f"listener {self.port}")
            text = text.split("# WebSocket listener")[0]
            with open(conf, "w") as fh:
                fh.write(text)
            self._proc = subprocess.Popen(["mosquitto", "-c", conf],
                                          stdout=open(self.log, "wb"),
                                          stderr=subprocess.STDOUT)
        else:
            out = subprocess.run(
                ["docker", "run", "-d", "-p", f"127.0.0.1:{self.port}:1883",
                 "-v", f"{BROKER_CONF}:/mosquitto/config/mosquitto.conf:ro",
                 "eclipse-mosquitto:2"], capture_output=True, text=True)
            if out.returncode:
                raise RuntimeError(f"docker broker failed: {out.stderr.strip()}")
            self._cid = out.stdout.strip()
        self.wait_ready(timeout)
        return self

    def wait_ready(self, timeout: float = 15.0):
        # `time.time()` here measures a DURATION, not a date: the loop is a deadline, and
        # its answer is the same at every hour of every day. Clock-reading, not
        # clock-dependent — see the ledger in `test_clock_dependence.py`.
        import socket
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", self.port), 1.0).close()
                return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError(f"broker never listened on :{self.port}")

    def stop(self):
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        if self._cid:
            subprocess.run(["docker", "rm", "-f", self._cid], capture_output=True)
            self._cid = ""


class Supervisor:
    """`mqtt/run.py` in a subprocess — the shipped entry point, not an in-test assembly.

    That matters for anything asserted about `config.build_app()` /
    `config.build_synthesizer()`: those precedence rules are only really exercised when
    the process reads its own environment, which is what an appliance does.
    """

    def __init__(self, log_dir: str, *, broker_port: int, data_dir: str, env=None):
        self.status_port = free_port()
        self.log = os.path.join(log_dir, "supervisor.log")
        self.env = dict(os.environ)
        self.env.update(
            PYTHONUNBUFFERED="1",                   # or the log is block-buffered + empty
            MOXIE_MQTT_HOST="127.0.0.1", MOXIE_MQTT_PORT=str(broker_port),
            MOXIE_STATUS_PORT=str(self.status_port),
            MOXIE_ALLOW_UNVERIFIED_BOTS="1",        # throwaway d_<uuid> per run, as in run_smoke.sh
            MOXIE_DATA_DIR=data_dir,
            MOXIE_STT="off",                        # nothing here speaks TO the robot
            # ...and nothing here needs a brain, so it does not demand one. `llm` (the
            # module default) now exits unless MOXIE_LLM_BASE_URL names an endpoint —
            # this repo ships no default gateway (config.require_llm_base_url) — which is
            # the same reason run_smoke.sh and sim/compose-smoke.env both pick `echo`.
            # A test that IS about the brain passes MOXIE_APP itself, as the live suites do.
            MOXIE_APP="echo",
        )
        self.env.update(env or {})
        self._proc = None

    def start(self, timeout: float = 60.0,
              ready_line: str = SUBSCRIBED_LINE) -> "Supervisor":
        """Boot `mqtt/run.py` and block until it can actually be talked to.

        The default is the SUBACK line, not `[runtime] broker connected`: the caller's
        very next move is to point a robot at this supervisor, and between the CONNACK and
        the SUBACK the supervisor is connected and DEAF — the robot's `/state` is dropped
        by the broker and the QoS-0 config push that answers it is never sent. See
        `_on_subscribe` in mqtt/supervisor/moxie_runtime.py; PR #143 fixed the mirror
        image in the robot. `ready_line=CONNECT_LINE` is for the one test that wants to
        stand in that gap on purpose.
        """
        self._proc = subprocess.Popen([sys.executable, os.path.join(REPO, "mqtt", "run.py")],
                                      cwd=REPO, env=self.env,
                                      stdout=open(self.log, "wb"),
                                      stderr=subprocess.STDOUT)
        self.wait_for(ready_line, timeout)
        return self

    def text(self) -> str:
        try:
            with open(self.log) as fh:
                return fh.read()
        except OSError:
            return ""

    def wait_for(self, needle: str, timeout: float = 30.0) -> str:
        # Deadline, not a date — see `wait_ready` above.
        deadline = time.time() + timeout
        while time.time() < deadline:
            body = self.text()
            if needle in body:
                return body
            if self._proc and self._proc.poll() is not None:
                raise RuntimeError(f"supervisor exited rc={self._proc.returncode}\n{body}")
            time.sleep(0.2)
        raise RuntimeError(f"supervisor never logged {needle!r}\n{self.text()}")

    def line_with(self, needle: str) -> str:
        for line in self.text().splitlines():
            if needle in line:
                return line
        return ""

    def stop(self):
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None


class Stack:
    """Broker + supervisor together, as a context manager."""

    def __init__(self, log_dir: str, *, env=None, data_dir: str | None = None):
        self.log_dir = str(log_dir)
        os.makedirs(self.log_dir, exist_ok=True)
        self.data_dir = data_dir or os.path.join(self.log_dir, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self.env = env or {}
        self.broker = None
        self.supervisor = None

    def __enter__(self) -> "Stack":
        self.broker = Broker(self.log_dir).start()
        try:
            self.supervisor = Supervisor(self.log_dir, broker_port=self.broker.port,
                                         data_dir=self.data_dir, env=self.env).start()
        except Exception:
            self.broker.stop()
            raise
        return self

    def __exit__(self, *exc):
        if self.supervisor:
            self.supervisor.stop()
        if self.broker:
            self.broker.stop()
        return False

    @property
    def port(self) -> int:
        return self.broker.port

    def restart_supervisor(self, *, env=None, timeout: float = 60.0) -> "Supervisor":
        """Stop `mqtt/run.py` and start a NEW one on the same broker and the same
        `MOXIE_DATA_DIR` — a real process restart, which is the only honest way to prove
        that durable state is durable.

        A fresh `MoxieRuntime` object over the same `JsonStore` proves the hydration
        *code path*, but it shares the test's interpreter: module-level caches, an
        `atexit` flush or a store the runtime happened to keep open would all survive it
        and nobody would notice. This does not — the old process is gone, and the new one
        is the shipped entry point reading its own environment again.

        The status port is re-picked (a `TIME_WAIT` socket on the old one would make the
        new supervisor's status server fail to bind, and that failure is best-effort and
        silent), so a caller holding the old `status_url` must re-read it from the
        returned `Supervisor`. `env` overlays the new process's environment; omitted, it
        inherits exactly what the first one had.
        """
        assert self.supervisor is not None, "nothing to restart"
        previous = dict(self.supervisor.env)
        self.supervisor.stop()
        merged = {k: v for k, v in previous.items() if k.startswith("MOXIE_")}
        merged.pop("MOXIE_STATUS_PORT", None)       # re-picked by Supervisor.__init__
        merged.update(env or {})
        self.supervisor = Supervisor(self.log_dir, broker_port=self.broker.port,
                                     data_dir=self.data_dir, env=merged)
        # A second process writing the same log path would truncate the first one's
        # evidence, so the restart gets its own file.
        self.supervisor.log = os.path.join(self.log_dir, "supervisor-restart.log")
        return self.supervisor.start(timeout)
