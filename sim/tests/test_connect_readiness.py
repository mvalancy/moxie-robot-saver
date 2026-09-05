"""The readiness line must not outrun the subscriptions — nor the broker's ACK of them.

`helpers_stack.Supervisor.start()` waits for `[runtime] broker connected` before it lets
a robot announce itself. For as long as `_on_connect` printed that line *before* calling
`subscribe`, the supervisor advertised readiness it did not have: the SIL robot's single
`/state` could land in the gap and go unheard, and `test_live_gateway_turn_e2e` failed as
"no config pushed within timeout" — intermittently, and *more often on a quiet box*,
because a busy one is slow enough to lose the race.

Found by the sixth integration pass while verifying the week soak (2026-09-04). It is
playbook rule 23's shape inside the runtime: a signal that was true of an earlier moment.
The assertion is on the ORDER OF EFFECTS, not on the source text, so a refactor that keeps
the bug cannot pass it.

**AND THE SAME BUG ONE HANDSHAKE LATER (2026-09-05).** Subscribing before printing made
the line honest about what it claimed; it did not make the claim useful, because
`subscribe()` does not subscribe. It generates a mid, queues a SUBSCRIBE packet and
returns — under `loop_forever()` the bytes go out on the network thread *after* this
callback — so `broker connected` has always meant *"we asked"*. A robot booted on it
publishes `/state` into a broker with no matching subscription; the config push that
answers a `/state` is QoS 0 and not retained, so the message is not late, it is **gone**.
HIL, on the promotion PR:

    ❌ scenario 'basic-conversation': 0/4 turns OK — no config pushed within timeout

with `motion-demo`, the *second* scenario, green at 4/4 in the same job. First fails,
second passes: a startup race, not a scenario bug. PR #143 fixed exactly this on the
robot's side of the same wire (`sim/tests/test_sil_handshake.py`); this is the
supervisor's.

The fix is a real `on_subscribe` and a **second** line,
`[runtime] subscriptions acknowledged by the broker`, printed from the SUBACK. The first
line keeps its meaning — `/status`'s `broker_connected`, the console card and the rc=5
guards below all still want the CONNACK — so nothing that already reads it was moved
under. The bottom half of this file is the order-of-effects proof for the new line.
"""
import io, os, sys
from contextlib import redirect_stdout

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))

import pytest
pytest.importorskip("paho.mqtt.client")


class _OrderRecordingClient:
    """Records every subscribe, and what stdout had said by the time it arrived.

    `subscribe` takes whatever paho takes — one topic, or the `[(topic, qos), …]` list the
    runtime now sends so that one SUBSCRIBE is answered by one SUBACK.
    """

    def __init__(self, out):
        self.out, self.subscribes = out, []

    def subscribe(self, topic, qos=0):
        self.subscribes.append((topic, self.out.getvalue()))
        return (0, 1)                       # (rc, mid), as paho returns

    def topics(self):
        """Every topic actually asked for, however it was batched."""
        out = []
        for topic, _ in self.subscribes:
            if isinstance(topic, str):
                out.append(topic)
            else:
                out.extend(t if isinstance(t, str) else t[0] for t in topic)
        return out


def _fresh_runtime():
    import moxie_runtime
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.types import ChildProfile

    class _App(MoxieApp):
        name = "echo"

    return moxie_runtime.MoxieRuntime(app=_App(), child=ChildProfile(nickname="Sam"))


def test_the_readiness_line_is_printed_only_after_every_subscription():
    rt = _fresh_runtime()
    out = io.StringIO()
    client = _OrderRecordingClient(out)
    with redirect_stdout(out):
        rt._on_connect(client, None, {}, 0)

    assert client.subscribes, "no subscription was made on a successful CONNACK"
    for topic, stdout_at_that_moment in client.subscribes:
        assert "broker connected" not in stdout_at_that_moment, (
            f"the runtime announced 'broker connected' BEFORE subscribing to {topic!r} — "
            "a harness that waits on that line will publish into a supervisor that is not "
            "listening yet (rule 23: a readiness signal true of an earlier moment)")
    assert "broker connected" in out.getvalue(), "it never announced readiness at all"


def test_a_refused_connack_neither_subscribes_nor_claims_connection():
    """The original bug this ordering lesson generalises from: rc=5 must do neither."""
    rt = _fresh_runtime()
    out = io.StringIO()
    client = _OrderRecordingClient(out)
    with redirect_stdout(out):
        rt._on_connect(client, None, {}, 5)
    assert client.subscribes == [], "a refused CONNACK subscribed anyway"
    assert "broker connected" not in out.getvalue(), "a refusal logged 'broker connected'"


# --------------------------------------------------------------------------- #
# THE SECOND HANDSHAKE: the line a harness may actually boot a robot on.
#
# Everything above is about the CONNACK. Everything below is about the SUBACK, and the
# distinction is a deleted message rather than a slow one — see the module docstring.
# --------------------------------------------------------------------------- #
SUBACK_LINE = "[runtime] subscriptions acknowledged by the broker"


def test_the_subscribed_line_is_not_printed_by_the_connack():
    """The whole bug in one assertion.

    `_on_connect` has done everything it can — it has asked. It must not say the broker
    answered, because the broker has not: the SUBSCRIBE packet is still queued.
    """
    rt = _fresh_runtime()
    out = io.StringIO()
    with redirect_stdout(out):
        rt._on_connect(_OrderRecordingClient(out), None, {}, 0)
    assert SUBACK_LINE not in out.getvalue(), (
        "the runtime claimed acknowledged subscriptions inside the CONNACK callback. "
        "`subscribe()` only queues a packet; the ack arrives in `_on_subscribe`, and a "
        "harness that boots a robot in between loses the robot's `/state` and the QoS-0 "
        "config push that would have answered it")
    assert not rt.subscriptions_acked.is_set(), \
        "`subscriptions_acked` was armed without a SUBACK"


def test_the_suback_is_what_prints_it_and_arms_the_flag():
    rt = _fresh_runtime()
    out = io.StringIO()
    with redirect_stdout(out):
        rt._on_connect(_OrderRecordingClient(out), None, {}, 0)
        rt._on_subscribe(None, None, 1, [0], None)
    assert rt.subscriptions_acked.is_set(), "a SUBACK did not arm `subscriptions_acked`"
    body = out.getvalue()
    assert SUBACK_LINE in body, (
        f"the SUBACK printed nothing. Every SIL harness now blocks on {SUBACK_LINE!r}; "
        f"a runtime that stops printing it hangs all of them for 40 s")
    # Ordering, not just presence: the readiness signal a robot is booted on must come
    # after the one that only says we asked.
    assert body.index("broker connected") < body.index(SUBACK_LINE)


def test_the_subscribed_line_is_flushed():
    """Same contract as the CONNACK line, same reason: every waiter greps a redirected,
    block-buffered stdout, so an unflushed readiness signal is a 40 s phantom hang."""
    events = []

    class _IO:
        def write(self, s):
            if s.strip():
                events.append(("write", s))
            return len(s)

        def flush(self):
            events.append(("flush", None))

    rt = _fresh_runtime()
    with redirect_stdout(_IO()):
        rt._on_subscribe(None, None, 1, [0], None)
    idx = next((i for i, (k, p) in enumerate(events) if k == "write" and SUBACK_LINE in p),
               None)
    assert idx is not None, f"the SUBACK line was never written: {events}"
    assert any(k == "flush" for k, _ in events[idx:]), (
        f"{SUBACK_LINE!r} was written but never flushed — print(..., flush=True)")


def test_one_subscribe_call_covers_every_topic_so_one_suback_is_enough():
    """Why `_on_subscribe` needs no counting.

    Four `subscribe()` calls are four SUBACKs, and a flag set on the first of them is the
    original bug wearing a callback. One list subscribe is one packet and one ack, which
    is what PR #143 did on the robot side.
    """
    rt = _fresh_runtime()
    out = io.StringIO()
    client = _OrderRecordingClient(out)
    with redirect_stdout(out):
        rt._on_connect(client, None, {}, 0)
    assert len(client.subscribes) == 1, (
        f"{len(client.subscribes)} subscribe calls — each gets its own SUBACK, so the "
        f"first ack would arm readiness while three subscriptions were still in flight. "
        f"Subscribe once with a [(topic, qos), …] list.")
    assert sorted(client.topics()) == sorted(rt.SUBSCRIPTIONS), (
        f"the one call does not cover every topic: {client.topics()}")


def test_a_disconnect_disarms_it_so_a_reconnect_must_earn_it_again():
    """A SUBACK is a fact about a socket, and the socket is gone.

    The SIL/CI brokers run clean sessions, so a reconnect re-subscribes from scratch. A
    latched flag would let the *second* connection's readiness be claimed by the first
    connection's ack — the same "true of an earlier moment" shape, one layer up.
    """
    rt = _fresh_runtime()
    out = io.StringIO()
    with redirect_stdout(out):
        rt._on_connect(_OrderRecordingClient(out), None, {}, 0)
        rt._on_subscribe(None, None, 1, [0], None)
        assert rt.subscriptions_acked.is_set()
        rt._on_disconnect(None, None, None, 7)
    assert not rt.subscriptions_acked.is_set(), (
        "`subscriptions_acked` survived a disconnect: the appliance believes it is "
        "subscribed on a socket that no longer exists")


def test_a_refused_subscription_is_not_readiness():
    """An ack can say no, and `0x80` is how it says it.

    A broker ACL that does not grant this credential `/devices/+/state`
    (security-broker-auth.md §2.2) answers the SUBSCRIBE with a failure code per refused
    filter. The supervisor is then genuinely deaf, so arming readiness on that SUBACK
    would be `broker connected rc=5` wearing a later callback — the same comfortable lie
    the rest of this file exists to prevent.
    """
    rt = _fresh_runtime()
    out = io.StringIO()
    with redirect_stdout(out):
        rt._on_connect(_OrderRecordingClient(out), None, {}, 0)
        rt._on_subscribe(None, None, 1, [0, 0, 128, 0], None)
    body = out.getvalue()
    assert not rt.subscriptions_acked.is_set(), \
        "a REFUSED subscription armed readiness"
    assert SUBACK_LINE not in body, \
        "the runtime announced acknowledged subscriptions it does not have"
    assert "REFUSED" in body, (
        f"a refused subscription was swallowed. The harness will now time out with no "
        f"reason above it, which is the diagnosis this line exists to give: {body!r}")
    assert any(n["kind"] == "error" for n in rt.recent), list(rt.recent)


def test_the_connack_callback_is_the_only_place_that_subscribes():
    """The fact the ack-without-counting rests on.

    `_on_subscribe` arms readiness on the first SUBACK it sees, which is sound only while
    exactly one SUBSCRIBE is ever sent per connection. A second `subscribe()` call
    anywhere in the runtime would let an unrelated ack arm it early, so this pins the
    property rather than trusting the reviewer who added the second call.
    """
    src = open(os.path.join(REPO, "mqtt", "supervisor", "moxie_runtime.py"),
               encoding="utf-8").read()
    calls = [ln.strip() for ln in src.splitlines()
             if ".subscribe(" in ln and not ln.strip().startswith("#")]
    assert calls == ["c.subscribe([(t, 0) for t in self.SUBSCRIPTIONS])"], (
        f"the runtime subscribes in more than one place: {calls}. Either fold it into "
        f"`_on_connect`'s single list call, or make `_on_subscribe` match the mid it is "
        f"waiting for — an unmatched SUBACK arming readiness is the bug again.")
