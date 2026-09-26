"""🔌 The SUPERVISOR must not be booted on a promise the broker has not kept.

Mirror image of `test_sil_handshake.py`, on the supervisor's side. `client.subscribe()`
only queues a SUBSCRIBE (sent later on paho's thread), and `[runtime] broker connected` is
printed right after it — "we asked", not "the broker agreed". A robot announcing in that
gap publishes `/state` to a broker with no matching subscription, so no config push is ever
generated (it is QoS 0, not retained): deleted, not delayed, and no timeout helps. Seen as
the FIRST scenario failing 0/4 with the second passing. The fix is a second readiness line
printed from `_on_subscribe`.

Sleeping inside `_on_connect` cannot reproduce this (the line is printed after the loop, so
it is delayed too). The gap is on the WIRE, so a TCP relay holds the SUBSCRIBE packet for
`HOLD_SUBSCRIBE_S`; `mqtt/run.py` runs unpatched.

1. §1 — booted on the SUBACK line, the supervisor serves the robot.
2. §2 — the teeth: the identical run booted on `broker connected` loses the config.

Needs a broker (hence `test_sil_*`). The hermetic halves are `test_connect_readiness.py` and
`test_harness_readiness.py`.

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

#: How long the supervisor's SUBSCRIBE is held on the wire. It only has to exceed a robot's
#: connect + announce (~0.1 s) — a `/state` with no subscription is dropped instantly — so
#: 3 s is two orders of magnitude of margin, not a stopwatch reading of the runner.
HOLD_SUBSCRIBE_S = 3.0

#: A config is answered within milliseconds of the announcement once anybody is listening
#: (`_device_connect` schedules the push on a 1.0 s settle timer). This is a ceiling on
#: that, generous enough that a failure means "never", not "slow".
CONFIG_WAIT_S = 8.0


# --------------------------------------------------------------------------- #
# A broker relay that holds SUBSCRIBE packets back
# --------------------------------------------------------------------------- #
def _split_packet(buf: bytes):
    """One MQTT control packet off the front of `buf`, or `(None, buf)` — just the type byte
    and the 1-4 byte varint remaining-length; contents are never needed."""
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
    """A TCP relay in front of the broker that delays only SUBSCRIBE packets by `delay_s`;
    everything else passes straight through. The supervisor connects here, robots to the
    real broker. Injected at the transport, so the claim is about the shipped `mqtt/run.py`.
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
    """The HIL red on demand: same supervisor, robot and relay, but booted on
    `[runtime] broker connected` — the announcement lands in the gap and no config ever
    exists. `got_config` waits longer than the hold, so a merely LATE config would arrive
    and fail this test.
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
