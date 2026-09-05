"""🔌 The SUPERVISOR must not be booted on a promise the broker has not kept.

**The finding (2026-09-05, promotion PR).** The HIL job went red with

    ❌ scenario 'basic-conversation': 0/4 turns OK — no config pushed within timeout

while `motion-demo` — the **second** scenario in the same job, against the same
supervisor — passed 4/4. First fails, second passes: that split is a startup race, not a
scenario bug, and it is the *mirror image* of the one PR #143 fixed in the robot
(`test_sil_handshake.py`). Same wire, other end:

* `client.subscribe(topic)` does not subscribe. It generates a mid, queues a SUBSCRIBE
  packet and returns; under `loop_forever()` the bytes leave on paho's network thread
  **after** `_on_connect` returns.
* `sim/run_scenarios.sh` and `sim/run_smoke.sh` booted their robots on
  `[runtime] broker connected`, which `_on_connect` prints immediately after that call —
  so the line meant *"we asked"*, never *"the broker agreed"*.
* Until 2026-09-05 the supervisor had no `on_subscribe` handler at all, so it had no way
  to know the difference.

A robot announcing in that window publishes `/devices/<id>/state` to a broker holding no
matching subscription. The supervisor answers a `/state` with a config push at **QoS 0
and not retained** (`moxie_runtime._publish`; QoS 1 is refused on purpose,
production-hardening.md §4.3), so nobody receives the announcement, no push is ever
generated, and nothing replays it. **The message is deleted, not delayed** — which is the
whole reason a bigger timeout cannot help, and why the fix is a second readiness line
rather than a bigger number.

**How the race is created here — and why the robot-side trick does NOT work.**
`test_sil_handshake.py` reproduces its race by sleeping inside `on_connect` before
subscribing. Doing that to the supervisor proves nothing: `_on_connect` prints its
readiness line *after* the subscribe loop, so a sleep there delays the line too and the
gap never opens. The supervisor's gap is on the **wire**, after the callback returns. So
this file puts a TCP relay in front of the broker and holds the SUBSCRIBE packet back for
`HOLD_SUBSCRIBE_S`. Nothing in the appliance is patched, nothing is monkeypatched, and
`mqtt/run.py` runs exactly as it ships: the SUBSCRIBE really is sent when the runtime
sends it and really does take that long to arrive, which is what a loaded CI runner does
to it for free — only bigger, and repeatable.

**What this file proves.**

1. §1 — the shipped supervisor, booted on the SUBACK line, serves a robot whose config
   push it would otherwise have thrown away.
2. §2 — the teeth: the identical run booted on `[runtime] broker connected` loses the
   config **outright**, so §1 cannot pass vacuously. This is the HIL failure, on demand.

Needs a broker (mosquitto binary or docker), which is why the file is named `test_sil_*`:
both CI tiers select the hermetic suite with `-k "not test_sil"`. The hermetic halves of
this contract — that the runtime prints the line only on the SUBACK, and that every
supervisor-booting script waits for *that* line — live in `test_connect_readiness.py` and
`test_harness_readiness.py`, which do run in CI.

    .venv/bin/python -m pytest sim/tests/test_sil_supervisor_readiness.py -q
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for _p in (REPO, os.path.join(REPO, "sim"), os.path.dirname(__file__)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytest.importorskip("paho.mqtt.client", reason="the handshake under test is a paho one")

import helpers_stack as S                                            # noqa: E402
from virtual_moxie import VirtualMoxie                               # noqa: E402

#: How long the supervisor's SUBSCRIBE packet is held on the wire.
#:
#: There is no settle timer to out-wait on this side — the robot-side race had to beat
#: `_device_connect`'s 1.0 s window, but a `/state` that arrives with no subscription in
#: place is dropped by the broker instantly and is never seen again. So the only thing
#: this number has to exceed is how long a robot takes to connect and announce (~0.1 s),
#: and three seconds is that with two orders of magnitude of margin — chosen so the test
#: cannot become a stopwatch reading of the runner.
HOLD_SUBSCRIBE_S = 3.0

#: A config is answered within milliseconds of the announcement once anybody is listening
#: (`_device_connect` schedules the push on a 1.0 s settle timer). This is a ceiling on
#: that, generous enough that a failure means "never", not "slow".
CONFIG_WAIT_S = 8.0


# --------------------------------------------------------------------------- #
# A broker relay that holds SUBSCRIBE packets back
# --------------------------------------------------------------------------- #
def _split_packet(buf: bytes):
    """One whole MQTT control packet off the front of `buf`, or `(None, buf)`.

    Fixed header: one type/flags byte, then a 1-4 byte varint remaining-length. We only
    ever need to know a packet's TYPE and its LENGTH, never its contents, so this is the
    whole parser.
    """
    if len(buf) < 2:
        return None, buf
    multiplier, length, i = 1, 0, 1
    while True:
        if i >= len(buf):
            return None, buf                     # length field itself is still in flight
        byte = buf[i]
        i += 1
        length += (byte & 0x7F) * multiplier
        if not byte & 0x80:
            break
        multiplier *= 128
        if multiplier > 128 ** 3:
            raise ValueError("malformed MQTT remaining length")
    end = i + length
    if len(buf) < end:
        return None, buf
    return buf[:end], buf[end:]


SUBSCRIBE = 8          # MQTT control packet type, high nibble of byte 0


class LateSubscribeProxy:
    """A TCP relay in front of the broker that delays SUBSCRIBE packets by `delay_s`.

    Everything else — CONNECT, PUBLISH, PINGREQ, and every byte coming back — is copied
    straight through, so the only thing that changes is the one packet whose lateness is
    the subject. The supervisor connects here; robots connect to the real broker, so a
    test can stand in the gap the relay opens.

    Deliberately at the transport, not in the appliance: a delay injected into
    `client.subscribe` would be a claim about a client double, and this is a claim about
    the shipped `mqtt/run.py` talking to a real mosquitto.
    """

    def __init__(self, upstream_port: int, delay_s: float):
        self.upstream_port = upstream_port
        self.delay_s = delay_s
        self.held = 0                        # SUBSCRIBE packets actually delayed
        self.port = S.free_port()
        self._srv = None
        self._stop = threading.Event()
        self._timers: list[threading.Timer] = []

    def start(self) -> "LateSubscribeProxy":
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", self.port))
        self._srv.listen(8)
        threading.Thread(target=self._accept_loop, daemon=True).start()
        return self

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                downstream, _ = self._srv.accept()
            except OSError:
                return
            try:
                upstream = socket.create_connection(("127.0.0.1", self.upstream_port), 10)
            except OSError:
                downstream.close()
                continue
            lock = threading.Lock()
            threading.Thread(target=self._pump_from_client, daemon=True,
                             args=(downstream, upstream, lock)).start()
            threading.Thread(target=self._pump_plain, daemon=True,
                             args=(upstream, downstream)).start()

    def _send(self, sock, data, lock):
        with lock:
            try:
                sock.sendall(data)
            except OSError:
                pass

    def _pump_from_client(self, src, dst, lock):
        """Client → broker, one MQTT packet at a time, holding the SUBSCRIBEs."""
        buf = b""
        try:
            while not self._stop.is_set():
                chunk = src.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while True:
                    packet, buf = _split_packet(buf)
                    if packet is None:
                        break
                    if packet[0] >> 4 == SUBSCRIBE and self.delay_s > 0:
                        self.held += 1
                        timer = threading.Timer(self.delay_s, self._send,
                                                (dst, packet, lock))
                        timer.daemon = True
                        self._timers.append(timer)
                        timer.start()
                    else:
                        self._send(dst, packet, lock)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.close()
                except OSError:
                    pass

    def _pump_plain(self, src, dst):
        lock = threading.Lock()
        try:
            while not self._stop.is_set():
                chunk = src.recv(65536)
                if not chunk:
                    break
                self._send(dst, chunk, lock)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.close()
                except OSError:
                    pass

    def stop(self):
        self._stop.set()
        for timer in self._timers:
            timer.cancel()
        if self._srv:
            try:
                self._srv.close()
            except OSError:
                pass
            self._srv = None


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def broker(tmp_path_factory):
    if not S.broker_available():
        pytest.skip("no mosquitto binary and no runnable docker — cannot boot a broker")
    b = S.Broker(str(tmp_path_factory.mktemp("sup-readiness"))).start()
    yield b
    b.stop()


@pytest.fixture
def proxy(broker):
    p = LateSubscribeProxy(broker.port, HOLD_SUBSCRIBE_S).start()
    yield p
    p.stop()


def _supervisor(tmp_path, proxy, *, ready_line):
    log_dir = str(tmp_path / "logs")
    data_dir = str(tmp_path / "data")
    os.makedirs(log_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)
    # The supervisor talks to the relay; robots talk to the broker itself.
    return S.Supervisor(log_dir, broker_port=proxy.port, data_dir=data_dir)\
            .start(timeout=60.0, ready_line=ready_line)


def _connected_robot(broker) -> VirtualMoxie:
    """A real `VirtualMoxie` — its real SUBACK-gated `announce()`, its real `got_config`."""
    vm = VirtualMoxie("127.0.0.1", broker.port, timeout=CONFIG_WAIT_S, verbose=False)
    vm.client.connect("127.0.0.1", broker.port, 30)
    vm.client.loop_start()
    return vm


# --------------------------------------------------------------------------- #
# 1. the rule: booted on the SUBACK, a 3 s-late SUBSCRIBE costs nothing
# --------------------------------------------------------------------------- #
def test_a_supervisor_whose_subscribe_is_late_still_serves_the_robot(tmp_path, broker, proxy):
    t0 = time.monotonic()
    sup = _supervisor(tmp_path, proxy, ready_line=S.SUBSCRIBED_LINE)
    booted = time.monotonic() - t0
    vm = _connected_robot(broker)
    try:
        assert proxy.held >= 1, (
            "the relay never saw a SUBSCRIBE to hold — this run proves nothing about "
            "lateness; check that the supervisor really connected through the proxy")
        assert booted >= HOLD_SUBSCRIBE_S * 0.5, (
            f"the boot returned in {booted:.2f}s with a SUBSCRIBE held for "
            f"{HOLD_SUBSCRIBE_S}s — the readiness wait cannot have been the SUBACK")
        assert S.CONNECT_LINE in sup.text(), "the CONNACK line vanished"
        assert vm.announce(), vm.errors
        assert vm.got_config.wait(CONFIG_WAIT_S), (
            "no config reached a robot that announced itself AFTER the supervisor "
            f"reported acknowledged subscriptions.\n--- supervisor ---\n{sup.text()}")
        assert (vm.config_payload or {}).get("pairing_status") == "paired", \
            vm.config_payload
    finally:
        vm.client.loop_stop()
        vm.client.disconnect()
        sup.stop()


# --------------------------------------------------------------------------- #
# 2. THE TEETH — booted on the CONNACK line, the same run loses the config
# --------------------------------------------------------------------------- #
def test_the_teeth_a_robot_booted_on_the_connack_line_never_gets_its_config(
        tmp_path, broker, proxy):
    """The HIL red, on demand.

    Identical supervisor, identical robot, identical relay: the *only* difference is
    which line the harness treated as readiness. Booted on `[runtime] broker connected`
    the announcement lands in the gap, the broker drops it, and the config that would
    have answered it is never generated — so the robot waits out its whole timeout for a
    message that does not exist. Without this, §1 above is a restatement of "MQTT works".

    Note what is NOT asserted: that the supervisor is slow. `got_config` is waited on for
    a full `CONFIG_WAIT_S`, which is longer than the SUBSCRIBE is held — so a config that
    was merely late would arrive and fail this test. It never does, because there is
    nothing to arrive.
    """
    sup = _supervisor(tmp_path, proxy, ready_line=S.CONNECT_LINE)
    vm = _connected_robot(broker)
    try:
        # The control: we really are standing in the gap, not after it.
        assert S.SUBSCRIBED_LINE not in sup.text(), (
            "the SUBACK had already landed when the robot announced — the relay did not "
            "hold the packet, so this run is not the race")
        assert vm.announce(), vm.errors
        assert not vm.got_config.wait(CONFIG_WAIT_S), (
            f"the robot DID get a config after announcing into the pre-SUBACK gap. "
            f"Either the relay stopped holding the SUBSCRIBE, or the supervisor grew a "
            f"second path to the robot — either way this test is no longer the teeth for "
            f"§1 and must be repaired, not deleted.\n--- supervisor ---\n{sup.text()}")
        # …and the supervisor is fine. It is not wedged, it did not crash, it simply
        # never heard the robot: the appliance's own log shows the connection it is
        # happy about and no robot at all.
        assert S.SUBSCRIBED_LINE in sup.text(), (
            "the SUBACK never arrived even after the hold expired — the supervisor is "
            "broken in some other way and this test is measuring that instead")
        assert "🤖 robot connected" not in sup.text(), (
            f"the supervisor logged a robot it cannot have heard:\n{sup.text()}")
    finally:
        vm.client.loop_stop()
        vm.client.disconnect()
        sup.stop()
