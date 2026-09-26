#!/usr/bin/env python3
"""
The SIL soak — *"a week in an hour"*, and honest about which half of that is true.

Build document:
`docs/architecture/backlog/production-hardening.md` **§5**, whose acceptance criteria
(§5.3, A1–A11) this file computes and prints, pass or fail, **never inferred**.

Read §5.4 before quoting any number this prints
-----------------------------------------------
It proves **our** half. It is an hour (or two minutes) at a raised rate against a
simulator, and it says nothing whatsoever about a week, about a real Moxie reconnecting,
or about a robot in a house. The rate substitution is a different claim from a duration
and is labelled as one everywhere it appears — including in the report this prints, which
carries §5.4 verbatim so a number cannot be quoted without it.

What it actually does
---------------------
Real mosquitto (a container), a real `mqtt/run.py`, real virtual robots on a real broker,
`MOXIE_APP=echo` so nothing reaches a gateway and the soak costs nothing.

* **Turns**, from N concurrent `VirtualMoxie`s, each filed by whether a fault was in
  flight (A1 counts only turns issued while the broker was up).
* **Broker restarts**, with the reconnect measured from *broker listening* to
  *supervisor re-subscribed* (A3) and every robot re-onboarded (A12).
* **Supervisor restarts**, by **SIGTERM**, exercising the clean-shutdown path and the
  roster resume that A4 measures.
* **Store contention**, deliberately (see below).
* **`SIGKILL` mid-write**, to prove no reader ever sees a truncated record (A6).
* **RSS and open file descriptors**, sampled from `/proc`, for A7 and A8 (a `flock` fd
  leaked per write would kill the appliance in week three).

The contention measurement, and why it is deliberate
----------------------------------------------------
`flock` has no queue — a `LOCK_NB` waiter takes whatever gap the holder leaves — so a
starved waiter timing out is a real, disclosed limit (A21). Its rate is load-dependent,
not only contention-dependent (measured 0–7 refusals per ~1 000–4 000 appends at the
default 2.0 s budget, from an idle box to 16 writers), so this harness reports a rate
instead of asserting a count, and adds the one check that turns it into a verdict:

    attempted  ==  items_on_disk  +  refusals

`items_on_disk + refusals < attempted` is a **silent loss**, which A5 sets at zero.
`refusals > 0` with the identity holding is a **recorded refusal**, which §3.2 point 4
accepts and A11 asks to be recorded rather than absent. The raw survival count cannot
tell those two apart.

Every duration here is a real elapsed second, but no **assertion** is a stopwatch: each
bar is a counter, a ratio or an identity. This is a deep-tier tool (K1), never fast-tier.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "sim"))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

BROKER_IMAGE = "eclipse-mosquitto:2"
BROKER_CONF = os.path.join(REPO, "sim", "broker", "ci-mosquitto.conf")


# --------------------------------------------------------------------------- #
# Profiles
# --------------------------------------------------------------------------- #
#
# `week` is §5.2's table. The other two are the same harness at rates that fit a laptop
# and a pre-push check — a soak nobody can run is a soak nobody runs.
PROFILES = {
    # ~1 minute. Everything happens at least once; nothing is measured well.
    "smoke": dict(minutes=1.0, robots=1, broker_restarts=1, supervisor_restarts=1,
                  turn_gap_s=0.4, writers=4, appends_per_writer=100, kills=3),
    # ~5 minutes. The default: enough restarts for a p95 to mean something, and the
    # contention level the handed-down measurement used (4 × 250 on one record).
    "quick": dict(minutes=5.0, robots=2, broker_restarts=4, supervisor_restarts=2,
                  turn_gap_s=0.5, writers=4, appends_per_writer=250, kills=10),
    # §5.2's `week`: 60 min · 24 broker / 4 supervisor restarts · 3 robots · 20 SIGKILLs.
    # 4 processes × 2 500 appends is §5.2's 10 000 at a higher contention level; the gap
    # gives ~4 000 turns (twice the ≥ 2 000 bar) — a heavy week's rate, not a benchmark.
    "week": dict(minutes=60.0, robots=3, broker_restarts=24, supervisor_restarts=4,
                 turn_gap_s=2.4, writers=4, appends_per_writer=2500, kills=20),
}

#: The collection the contention probe fights over. One record, deliberately: many writers
#: on the *same* thing is the only case `flock` serializes and the only one that starves.
PROBE_COLLECTION = "soak_probe"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket()
    s.settimeout(0.5)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def _wait_until(predicate, timeout: float, poll: float = 0.1) -> float | None:
    """Seconds until `predicate()` is true, or None on timeout. Polled, never slept."""
    started = time.monotonic()
    deadline = started + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return time.monotonic() - started
        except Exception:
            pass
        time.sleep(poll)
    return None


def _http_json(url: str, timeout: float = 3.0):
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


# --------------------------------------------------------------------------- #
# The stack
# --------------------------------------------------------------------------- #

#: Container name prefix, so a run killed hard (its `finally` never ran) leaves something
#: identifiable behind rather than an anonymous `eclipse-mosquitto` nobody dares remove.
BROKER_NAME_PREFIX = "moxie-soak-broker-"


def sweep_stale_brokers() -> list:
    """Remove brokers left behind by a previous soak that died before its cleanup —
    scoped to this prefix only; never a container a soak did not start."""
    out = subprocess.run(["docker", "ps", "-aq", "--filter",
                          f"name=^{BROKER_NAME_PREFIX}"],
                         capture_output=True, text=True)
    stale = [c for c in out.stdout.split() if c]
    for cid in stale:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
    return stale


class Broker:
    """A real mosquitto in a container, restartable on demand."""

    def __init__(self, port: int):
        self.port = port
        self.cid = ""
        self.name = f"{BROKER_NAME_PREFIX}{os.getpid()}"
        self.restarts = 0

    def start(self):
        self.cid = subprocess.check_output(
            ["docker", "run", "-d", "--name", self.name,
             "-p", f"127.0.0.1:{self.port}:1883",
             "-v", f"{BROKER_CONF}:/mosquitto/config/mosquitto.conf:ro",
             BROKER_IMAGE], text=True).strip()
        assert _wait_until(lambda: _port_open(self.port), 60) is not None, \
            "the broker never started listening"

    def restart(self) -> float:
        """Restart and return the seconds until it is listening again."""
        subprocess.run(["docker", "restart", "-t", "1", self.cid],
                       check=True, capture_output=True)
        took = _wait_until(lambda: _port_open(self.port), 60)
        assert took is not None, "the broker never came back"
        self.restarts += 1
        return took

    def stop(self):
        if self.cid:
            subprocess.run(["docker", "rm", "-f", self.cid], capture_output=True)
            self.cid = ""


class Supervisor:
    """A real `mqtt/run.py`, stopped with **SIGTERM** so every restart exercises the
    clean-shutdown path."""

    def __init__(self, broker_port: int, data_dir: str, status_port: int, log_path: str):
        self.broker_port, self.data_dir = broker_port, data_dir
        self.status_port, self.log_path = status_port, log_path
        self.proc = None
        self.restarts = 0
        self.dirty_stops = 0          # stops that needed a SIGKILL — an A10-adjacent fact

    @property
    def status_url(self) -> str:
        return f"http://127.0.0.1:{self.status_port}"

    def start(self):
        env = dict(os.environ)
        env.update(
            MOXIE_APP="echo", MOXIE_MQTT_HOST="127.0.0.1",
            MOXIE_MQTT_PORT=str(self.broker_port), MOXIE_STATUS_PORT=str(self.status_port),
            MOXIE_DATA_DIR=self.data_dir, MOXIE_ALLOW_UNVERIFIED_BOTS="1",
            PYTHONUNBUFFERED="1",
            # Creds blanked: `find_repo_dotenv()` falls back to the main worktree's
            # `mqtt/.env`, and a soak must not spend real gateway calls.
            MOXIE_LLM_API_KEY="", MOXIE_LLM_BASE_URL="",
            MOXIE_VOICE_BASE_URL="", MOXIE_STT_BASE_URL="")
        self.log = open(self.log_path, "a", buffering=1)
        self.proc = subprocess.Popen([sys.executable, os.path.join(REPO, "mqtt", "run.py")],
                                     cwd=REPO, env=env, stdout=self.log,
                                     stderr=subprocess.STDOUT)
        # SUBSCRIBED, not merely connected: between CONNACK and SUBACK the supervisor
        # hears nothing, so a robot announced then gets no config and the soak would
        # count its own startup race as a lost turn.
        assert _wait_until(self.subscribed, 90) is not None, \
            "the supervisor never reported acknowledged subscriptions"

    def connected(self) -> bool:
        try:
            return bool(_http_json(f"{self.status_url}/status", timeout=1)["broker_connected"])
        except Exception:
            return False

    def subscribed(self) -> bool:
        """CONNACK **and** SUBACK — the only state in which a robot can be heard.
        (`connected()` stays separate: A1 samples it as "was there a socket".)"""
        try:
            return bool(_http_json(f"{self.status_url}/status",
                                   timeout=1)["broker_subscribed"])
        except Exception:
            return False

    def status(self):
        return _http_json(f"{self.status_url}/status")

    def conn(self):
        return _http_json(f"{self.status_url}/conn?limit=0")

    def stop(self, *, clean: bool = True):
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.send_signal(signal.SIGTERM if clean else signal.SIGKILL)
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.dirty_stops += 1
            self.proc.kill()
            self.proc.wait(timeout=10)

    def restart(self) -> float:
        """SIGTERM, restart, and return the seconds until it can be heard (SUBACK)."""
        self.stop(clean=True)
        started = time.monotonic()
        self.start()
        self.restarts += 1
        return time.monotonic() - started

    # --- /proc sampling (A7, A8) ---
    def rss_kb(self):
        try:
            with open(f"/proc/{self.proc.pid}/status") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1])
        except (OSError, ValueError, IndexError, AttributeError):
            return None

    def fds(self):
        try:
            return len(os.listdir(f"/proc/{self.proc.pid}/fd"))
        except (OSError, AttributeError):
            return None



# --------------------------------------------------------------------------- #
# What "the broker was up" actually means
# --------------------------------------------------------------------------- #

class Outages:
    """The windows in which an injected fault was in flight, recorded BY THE INJECTOR.

    A1 counts only turns issued while the broker was up. Sampling `sup.connected` before
    and after a turn cannot see a restart that opens and closes inside the reply wait,
    so the injector records what it did and each turn is checked against the actual
    windows; the two samples are kept as a second signal for unscheduled outages.

    A window ends at the first moment a turn could be answered: for a broker restart,
    when the supervisor has resubscribed AND every robot is re-onboarded (how long that
    takes is A12's bar, not A1's); for a supervisor restart, when it reports a
    connection. Nothing wider — every second of a window is a second a defect is excused.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._closed: list = []          # [(start, end)] monotonic
        self._open: dict = {}            # token -> start, for a fault still in flight
        self._next = 0

    @contextlib.contextmanager
    def window(self):
        with self._lock:
            tok = self._next
            self._next += 1
            self._open[tok] = time.monotonic()
        try:
            yield
        finally:
            with self._lock:
                start = self._open.pop(tok)
                self._closed.append((start, time.monotonic()))

    def overlaps(self, a: float, b: float) -> bool:
        """Did any fault window touch [a, b]? An open window runs to +inf."""
        with self._lock:
            if any(start <= b for start in self._open.values()):
                return True
            return any(start <= b and a <= end for start, end in self._closed)

    def count(self) -> int:
        with self._lock:
            return len(self._closed) + len(self._open)


# --------------------------------------------------------------------------- #
# The robots
# --------------------------------------------------------------------------- #

class RobotDriver(threading.Thread):
    """One virtual robot, talking until told to stop.

    Every turn is stamped with whether a fault was in flight for any part of it (the
    injector's `Outages` record plus two `sup.connected` samples). A1 counts only the
    clean turns; A2 counts the ones a fault actually cost.
    """

    def __init__(self, host, port, gap_s, up_probe, outages, device_id=None):
        super().__init__(daemon=True)
        self.host, self.port, self.gap_s = host, port, gap_s
        self._up = up_probe
        self._outages = outages
        self.device_id = device_id or f"d_{uuid.uuid4()}"
        self.stop_flag = threading.Event()
        self.turns_up_ok = 0            # clean throughout, answered
        self.turns_up_lost = 0          # clean throughout, not answered → A1 fails
        self.turns_down = 0             # touched a fault, answered or not
        self.turns_down_lost = 0        # touched a fault AND was lost → A2's budget
        self.session_failures = 0       # could not connect / no config: not a turn at all
        self.errors: list = []

    def run(self):
        from virtual_moxie import VirtualMoxie
        while not self.stop_flag.is_set():
            vm = VirtualMoxie(self.host, self.port, self.device_id, timeout=8.0,
                              verbose=False)
            try:
                vm.client.connect(self.host, self.port, 30)
                vm.client.loop_start()
            except Exception as e:
                self.session_failures += 1
                self._backoff(str(e))
                continue
            try:
                # Announce only once the broker ACKED our subscriptions: the QoS-0
                # config answering the announce is not replayed if it races the SUBSCRIBE.
                if not vm.announce():
                    self.session_failures += 1
                    self._backoff("no suback")
                    continue
                if not vm.got_config.wait(8.0):
                    self.session_failures += 1
                    self._backoff("no config")
                    continue
                while not self.stop_flag.is_set():
                    if not self._turn(vm):
                        break               # the session is gone; rebuild it
                    if self.stop_flag.wait(self.gap_s):
                        break
            finally:
                try:
                    vm.client.loop_stop()
                    vm.client.disconnect()
                except Exception:
                    pass

    def _turn(self, vm) -> bool:
        """One prompt → reply. Returns False when the session looks dead."""
        was_up = self._up()
        t_start = time.monotonic()
        vm._reset_turn()
        try:
            vm.client.publish(vm.t_event("remote-chat"), json.dumps(
                {"event_id": str(uuid.uuid4()), "command": "prompt",
                 "backend": "router", "speech": "soak ping"}))
        except Exception as e:
            self.errors.append(f"publish: {e}")
            return False
        answered = vm.got_reply.wait(8.0)
        t_end = time.monotonic()
        still_up = self._up()
        # The injector's record, unioned with the two endpoint samples.
        if not was_up or not still_up or self._outages.overlaps(t_start, t_end):
            # A fault touched this turn: nothing for A1. Only a LOST one spends A2's
            # budget; one answered anyway is the system working through a fault.
            self.turns_down += 1
            if not answered:
                self.turns_down_lost += 1
            return answered or still_up
        if answered:
            self.turns_up_ok += 1
            return True
        self.turns_up_lost += 1
        self.errors.append("no reply while the broker was up")
        return True

    def _backoff(self, why: str):
        self.errors.append(f"session: {why}")
        self.stop_flag.wait(1.0)


# --------------------------------------------------------------------------- #
# The store probes
# --------------------------------------------------------------------------- #

_WRITER_SRC = r'''
import json, os, sys
sys.path.insert(0, os.path.join({repo!r}, "mqtt"))
from moxie_sdk.store import JsonStore
store = JsonStore(sys.argv[1])
device, collection, n, tag = sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
refused = 0
for i in range(n):
    if store.append(device, collection, f"{{tag}}-{{i}}") is None:
        refused += 1
print(json.dumps({{"attempted": n, "refused": refused,
                   "lock_timeouts": store.lock_timeouts}}))
'''


def contention_probe(data_dir: str, *, writers: int, appends: int,
                     timeout_s: float | None = None) -> dict:
    """`writers` processes × `appends` appends, all on **one** record.

    Returns the identity that distinguishes a recorded refusal from a silent loss,
    `attempted == on_disk + refused`. What must be zero is `lost`, not `refused`.
    """
    device = "d_probe"
    env = dict(os.environ)
    if timeout_s is not None:
        env["MOXIE_STORE_LOCK_TIMEOUT_S"] = str(timeout_s)
    src = _WRITER_SRC.format(repo=REPO)
    procs = [subprocess.Popen(
        [sys.executable, "-c", src, data_dir, device, PROBE_COLLECTION,
         str(appends), f"w{i}"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for i in range(writers)]
    started = time.monotonic()
    attempted = refused = unattempted = 0
    crashed = []
    for p in procs:
        out, err = p.communicate(timeout=900)
        if p.returncode != 0:
            # A crashed writer must be counted, not left out of `attempted`: the identity
            # would still balance and `lost` would read 0 on a run with a dead writer.
            crashed.append(err.strip()[-400:])
            unattempted += appends
            continue
        try:
            row = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            crashed.append(f"unparseable writer output: {out!r}")
            continue
        attempted += row["attempted"]
        refused += row["refused"]
    elapsed = time.monotonic() - started

    from moxie_sdk.store import JsonStore
    items = JsonStore(data_dir).read(device, PROBE_COLLECTION, [])
    on_disk = len(items) if isinstance(items, list) else 0
    unique = len(set(items)) if isinstance(items, list) else 0
    expected = writers * appends
    return {
        "writers": writers, "appends_per_writer": appends,
        "timeout_s": timeout_s, "elapsed_s": round(elapsed, 2),
        "expected": expected, "attempted": attempted, "refused": refused,
        "on_disk": on_disk, "duplicates": on_disk - unique,
        # The number the whole probe exists to compute. Anything but zero is an append
        # that reported success and reached no file — the silent loss A5 forbids.
        "lost": attempted - refused - on_disk,
        "refusal_rate": round(refused / attempted, 5) if attempted else 0.0,
        #: Appends a crashed writer never got to make. Reported beside `lost` so a run
        #: with a dead writer can never be read as a clean one.
        "crashed_writers": len(crashed), "unattempted": unattempted,
        "crashed": crashed,
    }


_KILLER_SRC = r'''
import os, sys, time
sys.path.insert(0, os.path.join({repo!r}, "mqtt"))
from moxie_sdk.store import JsonStore
store = JsonStore(sys.argv[1])
i = 0
while True:                       # append until somebody kills us mid-write
    store.append("d_kill", "kill_probe", "x" * 200 + str(i))
    i += 1
'''


def kill_probe(data_dir: str, *, kills: int) -> dict:
    """`SIGKILL` a writer while it is writing, `kills` times, then read the record back.

    A6: every file parses as the old or the new value, never a truncated one — the
    property `os.replace` provides and an in-place write would silently lose.
    """
    from moxie_sdk.store import JsonStore
    store = JsonStore(data_dir)
    src = _KILLER_SRC.format(repo=REPO)
    unreadable = 0
    torn = []
    strays = 0
    for _ in range(kills):
        p = subprocess.Popen([sys.executable, "-c", src, data_dir],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.25)                     # let it get into the read-modify-write loop
        p.kill()
        p.wait(timeout=10)
        raw = store.read("d_kill", "kill_probe", None)
        if raw is None:
            continue                          # nothing written yet: not a torn file
        if not isinstance(raw, list):
            unreadable += 1
            torn.append(type(raw).__name__)
            continue
        if any(not isinstance(x, str) for x in raw):
            unreadable += 1
    # A stray `.tmp` is not corruption, but it is unbounded growth if it recurs (A9).
    kill_dir = os.path.join(data_dir, "robots", "d_kill")
    if os.path.isdir(kill_dir):
        strays = sum(1 for n in os.listdir(kill_dir) if n.endswith(".tmp"))
    return {"kills": kills, "unreadable": unreadable, "torn": torn,
            "stray_tmp_files": strays}


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #

def run_soak(profile: str, *, minutes: float | None = None, port: int | None = None,
             keep_data: bool = False) -> dict:
    cfg = dict(PROFILES[profile])
    if minutes is not None:
        cfg["minutes"] = minutes
    duration = cfg["minutes"] * 60.0

    port = port or _free_port()
    status_port = _free_port()
    data_dir = tempfile.mkdtemp(prefix="moxie-soak-")
    log_path = os.path.join(data_dir, "supervisor.log")
    broker = Broker(port)
    sup = Supervisor(port, data_dir, status_port, log_path)
    result = {"profile": profile, "config": cfg, "data_dir": data_dir,
              "started_at": int(time.time())}
    drivers: list = []
    reconnects: list = []
    resumes: list = []
    samples: list = []

    print(f"▶️  soak · profile={profile} · {cfg['minutes']:.0f} min · "
          f"{cfg['robots']} robot(s) · broker :{port} · data {data_dir}", flush=True)
    swept = sweep_stale_brokers()
    if swept:
        print(f"  · swept {len(swept)} broker(s) left by a soak that died before its "
              f"cleanup", flush=True)
    try:
        broker.start()
        sup.start()
        t0 = time.monotonic()
        up = sup.connected
        outages = Outages()

        for _ in range(cfg["robots"]):
            d = RobotDriver("127.0.0.1", port, cfg["turn_gap_s"], up, outages)
            d.start()
            drivers.append(d)

        # The contention probe runs while the robots talk, on the supervisor's own data
        # dir — on an idle tree it would measure `flock`, not this appliance.
        probe_out: dict = {}
        probe_thread = threading.Thread(
            target=lambda: probe_out.update(contention_probe(
                data_dir, writers=cfg["writers"], appends=cfg["appends_per_writer"])),
            daemon=True)
        probe_thread.start()

        kill_out: dict = {}
        kill_thread = threading.Thread(
            target=lambda: kill_out.update(kill_probe(data_dir, kills=cfg["kills"])),
            daemon=True)
        kill_thread.start()

        # --- the fault schedule -------------------------------------------------
        events = _schedule(duration, cfg["broker_restarts"], cfg["supervisor_restarts"])
        baseline = None
        for when, what in events:
            _sleep_until(t0 + when)
            if baseline is None and time.monotonic() - t0 >= min(60.0, duration * 0.1):
                baseline = {"rss_kb": sup.rss_kb(), "fds": sup.fds(),
                            "at_s": round(time.monotonic() - t0, 1)}
            if what == "broker":
                # The window covers the restart AND the recovery, closing at
                # re-onboarding (see `Outages`).
                with outages.window():
                    listening = broker.restart()
                    # `resubscribed_after_s`: the SUBACK, not the CONNACK
                    took = _wait_until(sup.subscribed, 90)
                    # A12 — the robots talk continuously, so every one should be
                    # re-onboarded (config push + `app.on_connect`) within seconds.
                    reonboard = _wait_until(lambda: _all_robots_seen(sup), 15.0)
                reconnects.append({"listening_after_s": round(listening, 2),
                                   "resubscribed_after_s": None if took is None else round(took, 2),
                                   "reonboarded_after_s": None if reonboard is None else round(reonboard, 2),
                                   "ghosts": _ghosts(sup)})
                print(f"  · broker restart #{broker.restarts}: listening in "
                      f"{listening:.1f}s, supervisor back in "
                      f"{'NEVER' if took is None else f'{took:.1f}s'}", flush=True)
            else:
                known_before = sup.status().get("roster", {}).get("known", 0)
                # SIGTERM'd: it cannot answer until it reports a connection again.
                with outages.window():
                    took = sup.restart()
                # A4: every robot ever seen is re-pushed config without waiting for an
                # event. The resume has a 1 s settle timer: the bar (5 s) plus that.
                pushed = _wait_until(
                    lambda: _resumed(sup, known_before), 8.0)
                resumes.append({"restart_s": round(took, 2), "known_before": known_before,
                                "resumed_after_s": None if pushed is None else round(pushed, 2)})
                print(f"  · supervisor restart #{sup.restarts}: back in {took:.1f}s, "
                      f"roster resume {'NOT SEEN' if pushed is None else f'{pushed:.1f}s'}"
                      f" ({known_before} known)", flush=True)
            samples.append({"at_s": round(time.monotonic() - t0, 1),
                            "rss_kb": sup.rss_kb(), "fds": sup.fds()})

        _sleep_until(t0 + duration)
        for d in drivers:
            d.stop_flag.set()
        for d in drivers:
            d.join(timeout=20)
        probe_thread.join(timeout=900)
        kill_thread.join(timeout=300)

        final = {"rss_kb": sup.rss_kb(), "fds": sup.fds(),
                 "at_s": round(time.monotonic() - t0, 1)}
        status = sup.status()
        conn = sup.conn()
        result.update(
            elapsed_s=round(time.monotonic() - t0, 1),
            turns={"up_ok": sum(d.turns_up_ok for d in drivers),
                   "up_lost": sum(d.turns_up_lost for d in drivers),
                   "during_outage": sum(d.turns_down for d in drivers),
                   "lost_during_outage": sum(d.turns_down_lost for d in drivers),
                   "session_failures": sum(d.session_failures for d in drivers)},
            broker_restarts=broker.restarts, supervisor_restarts=sup.restarts,
            dirty_stops=sup.dirty_stops,
            reconnects=reconnects, resumes=resumes,
            baseline=baseline or final, final=final, samples=samples,
            contention=probe_out, kills=kill_out,
            status=status, conn=conn,
            log_findings=_scan_log(log_path))
    finally:
        for d in drivers:
            d.stop_flag.set()
        sup.stop(clean=True)
        broker.stop()
        if not keep_data:
            result["data_dir"] = f"{data_dir} (removed)"
            shutil.rmtree(data_dir, ignore_errors=True)
    return result


def _all_robots_seen(sup) -> bool:
    """Is every robot `/status` lists confirmed on the CURRENT broker connection
    (re-onboarded, not merely remembered)? False with no robots; `_ghosts()` names the rest.
    """
    try:
        robots = sup.status().get("robots") or []
    except Exception:
        return False
    return bool(robots) and all(r.get("seen_since_connect") for r in robots)


def _ghosts(sup) -> list:
    """Device ids `/status` lists that have not spoken since the broker came back."""
    try:
        return [r["device_id"] for r in (sup.status().get("robots") or [])
                if not r.get("seen_since_connect")]
    except Exception:
        return ["<status unreadable>"]


def _resumed(sup, known_before: int) -> bool:
    """Has the restarted supervisor re-pushed config from its roster yet (the `recent`
    ring carries the line)? An empty roster passes trivially, which is why `known_before`
    is recorded beside the verdict.
    """
    if known_before <= 0:
        return True
    try:
        recent = sup.status().get("recent") or []
    except Exception:
        return False
    return any("roster" in str(n.get("text", "")) for n in recent)


def _schedule(duration: float, broker_restarts: int, supervisor_restarts: int) -> list:
    """Fault times, evenly spread over the middle 80% of the run: the first 10% settles
    and takes the RSS baseline, the last 10% leaves room to observe the recovery."""
    events = []
    span = duration * 0.8
    start = duration * 0.1
    for i in range(broker_restarts):
        events.append((start + span * (i + 0.5) / max(1, broker_restarts), "broker"))
    for i in range(supervisor_restarts):
        events.append((start + span * (i + 0.25) / max(1, supervisor_restarts), "supervisor"))
    return sorted(events)


def _sleep_until(deadline: float):
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)


def _scan_log(path: str) -> dict:
    """A10 — unhandled exceptions or tracebacks in the supervisor log, counted, with
    the offending lines kept (a count is a bug report; a failed grep is not)."""
    tracebacks, errors = 0, []
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                if line.startswith("Traceback (most recent call last)"):
                    tracebacks += 1
                    errors.append(line.rstrip())
                elif "Exception in thread" in line or "Unhandled" in line:
                    tracebacks += 1
                    errors.append(line.rstrip()[:200])
    except OSError:
        return {"tracebacks": -1, "lines": ["log unreadable"]}
    return {"tracebacks": tracebacks, "lines": errors[:10]}


# --------------------------------------------------------------------------- #
# The report — §5.3, every bar, pass or fail, printed and never inferred
# --------------------------------------------------------------------------- #

def _p95(values) -> float:
    if not values:
        return 0.0
    import math
    ordered = sorted(values)
    return ordered[max(1, math.ceil(0.95 * len(ordered))) - 1]


def grade(r: dict) -> list:
    """Every §5.3 bar as `(id, what, measured, ok|None)`. `None` = not exercised."""
    bars = []
    t = r["turns"]
    issued_up = t["up_ok"] + t["up_lost"]
    bars.append(("A1", "turn success while the broker was up = 100%",
                 f"{t['up_ok']}/{issued_up}" + (f" ({t['up_ok'] / issued_up:.1%})" if issued_up else ""),
                 None if not issued_up else t["up_lost"] == 0))

    by_kind = (r["conn"].get("summary", {}).get("by_kind", {}) or {})
    drops = int(by_kind.get("publish_drop", 0))
    recorded = int(by_kind.get("disconnect", 0))
    outages = r["broker_restarts"]
    # A green A2 needs BOTH at most one turn LOST per robot per injected fault (a robot
    # can only lose the turn it was mid-way through — so losses spend the budget, not
    # crossings) AND a `disconnect` row for every broker restart: §5.3 says "an
    # unrecorded loss is a failure even if the count is 0".
    budget = r["config"]["robots"] * (outages + r["supervisor_restarts"])
    bars.append(("A2", f"turns lost to a drop ≤ 1 per robot per fault (≤{budget}), each recorded",
                 f"{t['lost_during_outage']} lost of {t['during_outage']} that crossed an "
                 f"outage (budget {budget}) · "
                 f"{recorded} disconnects and {drops} dropped publishes recorded "
                 f"for {outages} broker restarts",
                 None if not outages else
                 (t["lost_during_outage"] <= budget and recorded >= outages)))

    took = [x["resubscribed_after_s"] for x in r["reconnects"]
            if x["resubscribed_after_s"] is not None]
    missed = len(r["reconnects"]) - len(took)
    bars.append(("A3", "broker up → re-subscribed: p95 ≤ 3s, max ≤ 65s",
                 (f"p95 {_p95(took):.2f}s · max {max(took):.2f}s · n={len(took)}"
                  f"{f' · {missed} NEVER RECONNECTED' if missed else ''}") if took else "not exercised",
                 None if not took else (missed == 0 and _p95(took) <= 3.0 and max(took) <= 65.0)))

    seen = [x for x in r["resumes"] if x["known_before"] > 0]
    ok_resumes = [x for x in seen if x["resumed_after_s"] is not None]
    bars.append(("A4", "robots re-pushed config after a supervisor restart, within 5s",
                 (f"{len(ok_resumes)}/{len(seen)} restarts resumed the roster · "
                  f"max {max((x['resumed_after_s'] for x in ok_resumes), default=0):.2f}s")
                 if seen else "no restart had a non-empty roster",
                 None if not seen else (len(ok_resumes) == len(seen))))

    c = r["contention"]
    bars.append(("A5", "lost updates across processes = 0 (and no writer crashed)",
                 f"{c.get('lost')} lost of {c.get('attempted')} attempted "
                 f"({c.get('on_disk')} on disk + {c.get('refused')} refused)"
                 + (f" · {c['crashed_writers']} WRITER(S) CRASHED"
                    if c.get("crashed_writers") else ""),
                 None if not c else c.get("lost") == 0 and not c.get("crashed")))

    k = r["kills"]
    bars.append(("A6", "unreadable/truncated records after mid-write SIGKILLs = 0",
                 f"{k.get('unreadable')} unreadable after {k.get('kills')} kills "
                 f"· {k.get('stray_tmp_files')} stray .tmp",
                 None if not k else k.get("unreadable") == 0))

    base, fin = r["baseline"], r["final"]
    grew = None
    if base.get("rss_kb") and fin.get("rss_kb"):
        grew = (fin["rss_kb"] - base["rss_kb"]) / base["rss_kb"]
    bars.append(("A7", "supervisor RSS growth ≤ 10% (baseline → end)",
                 f"{base.get('rss_kb')}kB → {fin.get('rss_kb')}kB"
                 + (f" ({grew:+.1%})" if grew is not None else ""),
                 None if grew is None else grew <= 0.10))

    dfd = None
    if base.get("fds") is not None and fin.get("fds") is not None:
        dfd = fin["fds"] - base["fds"]
    bars.append(("A8", "open file descriptors ≤ +5 (the flock fds must not leak)",
                 f"{base.get('fds')} → {fin.get('fds')}" + (f" ({dfd:+d})" if dfd is not None else ""),
                 None if dfd is None else dfd <= 5))

    st = r["status"]
    conn_kept = int(r["conn"].get("summary", {}).get("count", 0))
    conn_cap = int(r["conn"].get("retention", {}).get("events", 0))
    bars.append(("A9", "bounded state at the end",
                 f"recent={len(st.get('recent', []))} robots={len(st.get('robots', []))} "
                 f"roster={st.get('roster', {}).get('known')} conn_events={conn_kept}/{conn_cap}",
                 conn_kept <= conn_cap and len(st.get("robots", [])) <= max(1, r["config"]["robots"])))

    reo = [x for x in r["reconnects"] if "reonboarded_after_s" in x]
    ok_reo = [x for x in reo if x["reonboarded_after_s"] is not None]
    ghosts = sorted({g for x in reo for g in (x.get("ghosts") or [])})
    bars.append(("A12", "every robot is re-onboarded after a broker restart "
                        "(no ghost left half-connected)",
                 (f"{len(ok_reo)}/{len(reo)} restarts re-onboarded every robot · "
                  f"max {max((x['reonboarded_after_s'] for x in ok_reo), default=0):.2f}s"
                  + (f" · STILL GHOSTS: {ghosts}" if ghosts else ""))
                 if reo else "not exercised",
                 None if not reo else (len(ok_reo) == len(reo) and not ghosts)))

    lg = r["log_findings"]
    bars.append(("A10", "unhandled exceptions / tracebacks in the supervisor log = 0",
                 f"{lg['tracebacks']}" + (f" — {lg['lines'][:3]}" if lg["lines"] else ""),
                 lg["tracebacks"] == 0))

    bars.append(("A11", "store writes refused on lock timeout — 0 at these rates, "
                        "and if non-zero, each one RECORDED",
                 f"{c.get('refused')} refused ({c.get('refusal_rate', 0):.2%}) · "
                 f"supervisor's own store_lock_timeouts={st.get('store_lock_timeouts')}",
                 # the verdict is the identity (nothing vanished), not a zero count
                 None if not c else c.get("lost") == 0))
    return bars


DISCLAIMER = """
  §5.4 — what this CANNOT prove, and it is most of what "a week" means:
    · that a real Moxie reconnects after a broker restart, or how quickly   (A5, hardware)
    · that a real Moxie accepts a /config push mid-session                  (A6, hardware)
    · that the re-prompt window is really ~20 s                             (A4, hardware)
    · that a real Moxie's client id is stable across reconnects             (A17, hardware)
    · anything at all about a week. This is an hour (or less) at a RAISED RATE against a
      simulator. The rate substitution is a different claim from a duration, and no Moxie
      has ever been on this broker — not for a week, not for an hour.
"""


def report(r: dict) -> bool:
    bars = grade(r)
    print("\n" + "=" * 78)
    print(f"SOAK REPORT · profile={r['profile']} · {r['elapsed_s']:.0f}s elapsed")
    print("=" * 78)
    t = r["turns"]
    print(f"  turns: {t['up_ok']} answered while up · {t['up_lost']} lost while up · "
          f"{t['during_outage']} crossed an outage ({t['lost_during_outage']} of them lost) · "
          f"{t['session_failures']} session failures")
    print(f"  faults injected: {r['broker_restarts']} broker restarts · "
          f"{r['supervisor_restarts']} supervisor restarts (SIGTERM; "
          f"{r['dirty_stops']} needed a SIGKILL) · {r['kills'].get('kills')} mid-write SIGKILLs")
    c = r["contention"]
    print(f"  store contention: {c.get('writers')} processes × {c.get('appends_per_writer')} "
          f"appends on ONE record, timeout="
          f"{c.get('timeout_s') or os.environ.get('MOXIE_STORE_LOCK_TIMEOUT_S') or '2.0 (default)'}s")
    print(f"    attempted {c.get('attempted')} = on disk {c.get('on_disk')} + refused "
          f"{c.get('refused')} + LOST {c.get('lost')}   (lost must be 0)")
    if c.get("crashed_writers"):
        print(f"    ⚠️  {c['crashed_writers']} writer process(es) CRASHED — "
              f"{c.get('unattempted')} appends never attempted: {c['crashed'][0][-200:]}")
    print("-" * 78)
    failed = 0
    unproven = 0
    for bar_id, what, measured, ok in bars:
        if ok is None:
            mark, unproven = "  ⚪", unproven + 1
        elif ok:
            mark = "  ✅"
        else:
            mark, failed = "  ❌", failed + 1
        print(f"{mark} {bar_id}  {what}")
        print(f"        measured: {measured}")
    print("-" * 78)
    verdict = ("✅ SOAK PASSED" if not failed else f"❌ SOAK FAILED — {failed} bar(s) below the line")
    print(f"{verdict} · {len(bars) - failed - unproven} met · {failed} failed · "
          f"{unproven} not exercised")
    print(DISCLAIMER)
    return failed == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--profile", default="quick", choices=sorted(PROFILES))
    ap.add_argument("--minutes", type=float, default=None,
                    help="override the profile's wall duration")
    ap.add_argument("--port", type=int, default=None, help="broker port (default: free)")
    ap.add_argument("--json", default="", help="write the full result to this path")
    ap.add_argument("--keep-data", action="store_true",
                    help="keep the scratch MOXIE_DATA_DIR for inspection")
    ap.add_argument("--only-contention", action="store_true",
                    help="run ONLY the store contention probe (no broker, no supervisor) — "
                         "the measurement, on its own, in seconds")
    ap.add_argument("--writers", type=int, default=4)
    ap.add_argument("--appends", type=int, default=250)
    ap.add_argument("--lock-timeout", type=float, default=None)
    args = ap.parse_args()

    if args.only_contention:
        data_dir = tempfile.mkdtemp(prefix="moxie-contention-")
        try:
            out = contention_probe(data_dir, writers=args.writers, appends=args.appends,
                                   timeout_s=args.lock_timeout)
        finally:
            shutil.rmtree(data_dir, ignore_errors=True)
        print(json.dumps(out, indent=2))
        # `lost` is the failure; `refused` is a disclosed, recorded limit (A21).
        return 0 if out["lost"] == 0 and not out["crashed"] else 1

    result = run_soak(args.profile, minutes=args.minutes, port=args.port,
                      keep_data=args.keep_data)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(result, fh, indent=2, default=str)
        print(f"(full result → {args.json})")
    return 0 if report(result) else 1


if __name__ == "__main__":
    sys.exit(main())
