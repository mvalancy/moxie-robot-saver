"""🤝 A robot must not announce itself before the broker can answer it.

`client.connect()` does not wait for CONNACK, and `on_connect` (where every client here
subscribes) runs later on paho's network thread. Publishing `/devices/<id>/state` from the
calling thread right after connecting can therefore reach the supervisor before our
SUBSCRIBE exists. The supervisor answers with `/config` at QoS 0, not retained
(`moxie_runtime._publish`; QoS 1 refused per production-hardening.md §4.3), so the answer is
delivered to nobody and never replayed — a DELETED config, not a slow one, which no timeout
can fix. The fix: the handshake observes the SUBACK (`VirtualMoxie.announce`).

Small injected delays do NOT reproduce it: the real supervisor pushes config on a 1.0 s
settle timer, absorbing any SUBSCRIBE delay under ~1 s (measured: 0/4 lost up to 1100 ms,
1/4 at 1500 ms, all lost at 3000 ms). So this file's fake cloud answers IMMEDIATELY, making
the question purely ordinal — did the subscription exist when the answer was sent.

Proven three ways:
1. the shipped `VirtualMoxie` survives a SUBSCRIBE 1.5 s late (§1);
2. the replaced idiom does NOT survive it, so §1 cannot pass vacuously (§2);
3. no SIL client in the tree announces without waiting for its SUBACK (§3).

Needs a broker only (hence `test_sil_*`); the "cloud" is a few lines of paho.

    .venv/bin/python -m pytest sim/tests/test_sil_handshake.py -q
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for _p in (REPO, os.path.join(REPO, "sim"), os.path.dirname(__file__)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytest.importorskip("paho.mqtt.client", reason="the handshake under test is a paho one")

import paho.mqtt.client as mqtt                                      # noqa: E402
import helpers_stack as S                                            # noqa: E402
from virtual_moxie import VirtualMoxie                               # noqa: E402

#: How late the robot's SUBSCRIBE is made to be. Larger than the supervisor's 1.0 s
#: settle timer on purpose — this is the size of the window the appliance really has, not
#: a number picked to make a test pass.
LATE_SUBSCRIBE_S = 1.5

#: How long a config is waited for. Every wait in this file is bounded by an event that
#: the *fake cloud below* publishes within milliseconds of the announcement, so this is a
#: ceiling on a sub-second answer and never a measurement of the machine.
CONFIG_WAIT_S = 10.0


# --------------------------------------------------------------------------- #
# A cloud that answers a /state the way the supervisor does
# --------------------------------------------------------------------------- #
class InstantCloud:
    """Answers each `/devices/+/state` with a QoS-0, non-retained `/config` IMMEDIATELY (not
    on the supervisor's settle timer, which would reintroduce timing slack). Waits for its
    own SUBACK before reporting ready.
    """

    def __init__(self, port: int):
        self.answered: list[str] = []
        self._ready = threading.Event()
        self._sub_mid = None
        self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id=f"cloud-{uuid.uuid4()}")
        self.c.on_connect = self._on_connect
        self.c.on_subscribe = self._on_subscribe
        self.c.on_message = self._on_message
        self.c.connect("127.0.0.1", port, 30)
        self.c.loop_start()
        if not self._ready.wait(30):
            raise RuntimeError("the fake cloud never got its own SUBACK")

    def _on_connect(self, c, u, flags, rc, props=None):
        self._sub_mid = c.subscribe("/devices/+/state")[1]

    def _on_subscribe(self, c, u, mid, reason_codes=None, properties=None):
        if mid == self._sub_mid:
            self._ready.set()

    def _on_message(self, c, u, msg):
        device_id = msg.topic.split("/")[2]
        self.answered.append(device_id)
        # QoS 0, retain=False — the supervisor's own publish shape.
        c.publish(f"/devices/{device_id}/config",
                  json.dumps({"pairing_status": "paired", "device_id": device_id}),
                  qos=0, retain=False)

    def close(self):
        try:
            self.c.loop_stop()
            self.c.disconnect()
        except Exception:
            pass


@pytest.fixture(scope="module")
def broker(tmp_path_factory):
    if not S.broker_available():
        pytest.skip("no mosquitto binary and no runnable docker — cannot boot a broker")
    b = S.Broker(str(tmp_path_factory.mktemp("handshake"))).start()
    yield b
    b.stop()


@pytest.fixture
def cloud(broker):
    c = InstantCloud(broker.port)
    yield c
    c.close()


def _make_subscribe_late(client, seconds: float):
    """Delay the SUBSCRIBE as a loaded runner does — inside `on_connect` on paho's thread,
    after CONNACK — by patching the client, so the shipped `_on_connect` and announcement
    path run unchanged.
    """
    real = client.subscribe
    first = {"done": False}

    def late(*a, **kw):
        if not first["done"]:
            first["done"] = True
            time.sleep(seconds)
        return real(*a, **kw)

    client.subscribe = late


# --------------------------------------------------------------------------- #
# 1. the rule: a late SUBSCRIBE delays the announcement, it does not lose the answer
# --------------------------------------------------------------------------- #
def test_a_robot_whose_subscribe_is_late_still_receives_its_config(broker, cloud):
    """The shipped `VirtualMoxie`, with its SUBSCRIBE 1.5 s late — longer than the
    supervisor's whole 1.0 s settle window — still hears the answer, because it does not
    speak until the broker says it can hear."""
    vm = VirtualMoxie("127.0.0.1", broker.port, timeout=CONFIG_WAIT_S, verbose=False)
    vm.client.connect("127.0.0.1", broker.port, 30)
    _make_subscribe_late(vm.client, LATE_SUBSCRIBE_S)
    vm.client.loop_start()
    try:
        assert vm.announce(), vm.errors
        assert vm.got_config.wait(CONFIG_WAIT_S), (
            "the config was published to a robot that could not hear it — the "
            f"handshake announced before its SUBACK. cloud answered: {cloud.answered}")
        assert (vm.config_payload or {}).get("pairing_status") == "paired", \
            vm.config_payload
    finally:
        vm.client.loop_stop()
        vm.client.disconnect()


def test_the_announcement_really_did_wait_for_the_suback(broker, cloud):
    """The control for the control. If `announce()` returned before the SUBACK, the test
    above could pass merely because the fake cloud happened to be slow — so this asserts
    the ORDER directly: `subscribed` is set by the broker's SUBACK, and it is set before
    `/state` is on the wire."""
    vm = VirtualMoxie("127.0.0.1", broker.port, timeout=CONFIG_WAIT_S, verbose=False)
    vm.client.connect("127.0.0.1", broker.port, 30)
    _make_subscribe_late(vm.client, LATE_SUBSCRIBE_S)
    vm.client.loop_start()
    try:
        assert not vm.subscribed.is_set(), "the SUBACK cannot have landed yet"
        t0 = time.monotonic()
        assert vm.announce(), vm.errors
        waited = time.monotonic() - t0
        assert vm.subscribed.is_set(), "announced without a SUBACK"
        assert waited >= LATE_SUBSCRIBE_S * 0.5, (
            f"announce() returned in {waited:.2f}s — it cannot have waited for a "
            f"SUBSCRIBE that was held for {LATE_SUBSCRIBE_S}s")
    finally:
        vm.client.loop_stop()
        vm.client.disconnect()


# --------------------------------------------------------------------------- #
# 2. THE TEETH — the idiom this replaced loses the config outright
# --------------------------------------------------------------------------- #
def test_the_teeth_the_pre_change_handshake_loses_the_config(broker, cloud):
    """The teeth: the real `VirtualMoxie` with only the replaced line restored —

        self.client.publish(self.t_state, json.dumps({...}))     # instead of announce()

    — must FAIL to receive a config under the same 1.5 s delay.
    """
    vm = VirtualMoxie("127.0.0.1", broker.port, timeout=CONFIG_WAIT_S, verbose=False)
    vm.client.connect("127.0.0.1", broker.port, 30)
    _make_subscribe_late(vm.client, LATE_SUBSCRIBE_S)
    vm.client.loop_start()
    try:
        vm.client.publish(vm.t_state, json.dumps(              # ← the pre-change line
            {"software_version": "24.10.803", "state": "config"}))
        assert not vm.got_config.wait(CONFIG_WAIT_S), (
            "the PRE-CHANGE handshake received the config, so §1 proves nothing. Either "
            "the cloud stopped answering at QoS 0, or something now retains or replays "
            "the config — in which case say so here and delete these teeth deliberately, "
            "rather than leaving a guard that cannot fail.")
        # …and the message really was SENT. Without this, "the robot heard nothing" would
        # also be satisfied by a cloud that never answered, which is a different bug.
        assert vm.device_id in cloud.answered, (
            "the fake cloud never saw the /state, so nothing was lost — this teeth block "
            f"proved nothing. answered={cloud.answered}")
    finally:
        vm.client.loop_stop()
        vm.client.disconnect()


# --------------------------------------------------------------------------- #
# 3. the class, not the instance — no SIL client may announce itself deaf
# --------------------------------------------------------------------------- #
#: Every `.py` under `sim/` is swept; a file is in scope when it drives a real broker
#: (`loop_start()`) AND publishes a `…/state` topic — either the literal or `t_state`
#: (`VirtualMoxie`'s property name). No hand-written list to rot.
_STATE_TOPIC = ('/state"', "t_state")


def _sil_clients():
    root = os.path.join(REPO, "sim")
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".venv", "node_modules", "web", "artifacts")]
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            src = open(path, encoding="utf-8").read()
            if "loop_start()" not in src:
                continue
            if not any("publish" in ln and any(t in ln for t in _STATE_TOPIC)
                       for ln in _publish_lines(src)):
                continue
            yield os.path.relpath(path, REPO), src


def _publish_lines(src: str):
    """`publish(` calls, rejoined across the line breaks this repo's 90-column style puts
    inside them — otherwise a topic on line 1 and its payload on line 2 read as two
    statements and half of them are missed."""
    out, buf = [], ""
    for line in src.splitlines():
        buf = (buf + " " + line.strip()) if buf else line.strip()
        if buf.count("(") <= buf.count(")"):
            out.append(buf)
            buf = ""
    return out


def test_every_sil_client_waits_for_its_suback_before_it_announces():
    """The class rule (sibling of `test_harness_readiness.py`'s script rule): a file that
    drives a real broker and publishes `/state` must wire `on_subscribe` or delegate to
    `VirtualMoxie.announce`.
    """
    clients = list(_sil_clients())
    assert clients, ("the sweep found no SIL clients at all — the shape it looks for has "
                     "changed and this guard is now vacuous")
    offenders = [rel for rel, src in clients
                 if "on_subscribe" not in src and ".announce(" not in src]
    assert not offenders, (
        "these publish a robot's /state over a real broker without ever waiting for the "
        "SUBACK that makes the reply audible — the config answering it is QoS 0 and not "
        "retained, so losing the race deletes the message rather than delaying it:\n  "
        + "\n  ".join(offenders))


def test_the_sweep_sees_the_clients_it_is_supposed_to_see():
    """Teeth for the sweep. A regex guard that silently matches nothing is the failure
    mode every ratchet in this repo has had at least once, so the four clients that exist
    today are named here — not as the allowlist (the sweep above is), but as proof the
    sweep is looking at them."""
    found = {rel for rel, _ in _sil_clients()}
    for expected in ("sim/virtual_moxie.py",
                     "sim/tools/first_audio_ab.py",
                     "sim/tests/test_sil_performance_e2e.py",
                     "sim/tests/test_sil_durable_telemetry.py"):
        assert expected in found, (f"{expected} is a SIL client and the sweep missed it; "
                                   f"it found {sorted(found)}")
