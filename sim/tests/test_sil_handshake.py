"""🤝 A robot must not announce itself before the broker can answer it.

**The finding (2026-09-04, SIL contention pass).** The `sil` job produced two
intermittent reds on PR diffs that could not touch them, both green on re-run, neither
reproducible locally. One of them was twelve setup errors reading

    RuntimeError: no paired config pushed within timeout        (a 60 s wait)

…while the supervisor in the very same run had logged

    [runtime] → pushed config to d_… (pairing_status=paired)

Two halves of one appliance disagreeing about whether a config exists. It is not a slow
config, it is a **deleted** one, and the deletion is in the robot's own handshake:

* `client.connect()` writes CONNECT and returns; it does **not** wait for CONNACK.
* `loop_start()`'s network thread reads the CONNACK and only then runs `on_connect`,
  which is where every client in this repo sends its SUBSCRIBE.
* the caller's very next line published `/devices/<id>/state` from the **calling**
  thread — so the broker could be handing our announcement to the supervisor while our
  SUBSCRIBE was still an unscheduled callback.

The supervisor answers a `/state` with `/config` at **QoS 0 and not retained**
(`moxie_runtime._publish`; QoS 1 is refused on purpose by production-hardening.md §4.3).
A QoS-0 message with no matching subscription is delivered to nobody and never replayed.
So the loser of that race waits out its entire timeout for a message that no longer
exists — which is exactly why **raising the timeout cannot fix it**, and why the fix is
to make the handshake *observe* the SUBACK instead of assuming it.

**WHY A SMALL INJECTED DELAY DOES NOT REPRODUCE IT — read this before trying.** The
obvious experiment is to sleep inside `on_connect` and watch the config go missing, and
it *fails*, which is how this was misdiagnosed once already. The supervisor does not
answer a `/state` at once: `_device_connect` schedules `_push_config` on a **1.0 s settle
timer** (`moxie_runtime.MoxieRuntime` — `threading.Timer(1.0, _settle)`). So the robot has
a whole second of slack it did not ask for, and any injected delay *inside* that second is
absorbed with nothing to see. Measured against the real stack on 2026-09-04:

    subscribe delayed    0 ms → 0/4 robots lost the config   (waits ≈ 1.02 s)
    subscribe delayed  100 ms → 0/4 robots lost the config
    subscribe delayed  500 ms → 0/4 robots lost the config   ← the experiment that "clears" it
    subscribe delayed 1100 ms → 0/4 robots lost the config
    subscribe delayed 1500 ms → 1/4 robots lost the config   ← the margin runs out
    subscribe delayed 3000 ms → paired=False after 35.00 s, configs_seen=[]

with the supervisor's own log for that last run reading

    [runtime] 🤖 robot connected: d_f51eb0b8-…
    [runtime] → pushed config to d_f51eb0b8-… (pairing_status=paired)

— one push, sent, gone. A 0.5 s injection is therefore not evidence of anything; the
threshold is the settle timer, and the failing region starts past it.

That is also why this file's fake cloud answers **immediately** rather than imitating the
settle timer: with the timer in the picture the question is "was the SUBSCRIBE more or
less than one second late", which is a measurement of the runner. Without it the question
is purely ordinal — *did the subscription exist when the answer was sent* — which is the
property actually under test, and it is decided by ordering rather than by speed.

**What this file proves.** Not the fix in one client — the *rule*, in three ways:

1. the shipped `VirtualMoxie` survives a SUBSCRIBE that is 1.5 s late (§1);
2. the idiom it replaced does **not** survive the same 1.5 s, so §1 cannot pass
   vacuously (§2 — the teeth);
3. no SIL client in the tree announces itself without waiting for its SUBACK (§3), so
   the next one written cannot quietly reintroduce it.

Needs a broker and nothing else — no supervisor, no brain, no network. The "cloud" here
is nine lines of paho that answers a `/state` the way the supervisor does: QoS 0, not
retained, immediately. The file is named `test_sil_*` because it needs that broker, which
is what both CI tiers' `-k "not test_sil"` hermetic selection means.

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
    """Subscribes to `/devices/+/state`; answers each one with a QoS-0, non-retained
    `/devices/<id>/config` **immediately**.

    Immediately, and not on the supervisor's 1.0 s settle timer, because the settle timer
    is the *slack* this test is trying to remove from the picture: with it, whether the
    race is lost depends on how late the SUBSCRIBE is relative to one second, and this
    file would be measuring the runner again. Answering at once makes the question purely
    ordinal — did the subscription exist when the answer was sent — which is the actual
    property under test.

    It waits for its own SUBACK before reporting ready, for the same reason everything
    else in this file does.
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
    """Delay the SUBSCRIBE the way a loaded runner does: inside `on_connect`, on paho's
    network thread, after the CONNACK has already been read.

    Patching the client rather than sleeping in the test is deliberate — it leaves the
    *shipped* `_on_connect` and the *shipped* announcement path running exactly as they
    ship, and moves only the thing the runner actually moves.
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
    """Run the handshake **this repo shipped until 2026-09-04** and require it to FAIL.

    Not a hand-rolled paho client: the object below is the real `VirtualMoxie`, with its
    real `_on_connect`, its real `_on_message` and its real `got_config` event. The only
    thing restored is the one line the fix replaced —

        self.client.publish(self.t_state, json.dumps({...}))     # instead of announce()

    — which is exactly what `run_smoke`, `run_scenario`, `run_unpaired`, `run_queries`,
    the telehealth run and the vision run all opened with. So this is a test that goes RED
    against the pre-change `sim/virtual_moxie.py` and green after it, which is the only
    thing that makes §1 above a proof rather than a restatement of "MQTT works".

    Verified by reverting: with `git stash` holding the fix, this same body received no
    config and §1 could not even be expressed (there was no `announce()` to call).
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
#: Every `.py` under `sim/` is swept. A file is IN SCOPE when it drives a real broker
#: (`loop_start()`) **and** publishes a `…/state` topic — i.e. when it is a SIL client
#: performing this exact handshake. Everything else (the in-process loopback tests, the
#: doc guards) is untouched by the rule and is not listed anywhere, so the sweep cannot
#: rot into a stale allowlist the way a hand-written file list would.
#:
#: Both spellings of the topic count: the literal `…/state"` and `t_state`, the property
#: `VirtualMoxie` names it by. Matching only the literal is how the first draft of this
#: sweep silently skipped the very client the whole finding came from.
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
    """The generalisation, shaped like `test_harness_readiness.py`: that file made "a SIL
    *script* must wait for the supervisor, never sleep at it" a rule about the class, and
    this one does the same for the client half of the same handshake.

    The rule as asserted: a file that drives a real broker and publishes a `/state` must
    either wire `on_subscribe` (and therefore have a SUBACK to wait on) or delegate the
    announcement to `VirtualMoxie.announce`, which does.
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
