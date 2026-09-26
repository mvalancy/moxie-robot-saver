"""The readiness lines must not outrun the subscriptions — nor the broker's ACK of them.

1. `[runtime] broker connected` (CONNACK) is printed only AFTER `subscribe` is called — else
   a robot's single `/state` could land before any subscription existed.
2. `subscribe()` only queues a SUBSCRIBE (sent on the network thread after the callback), so
   "broker connected" means "we asked". A robot booted on it can lose its QoS-0,
   non-retained config push outright (seen as the first HIL scenario failing 0/4 with the
   second green). So `[runtime] subscriptions acknowledged by the broker` is printed from a
   real `on_subscribe` SUBACK, and harnesses boot robots on that.

`broker connected` keeps its meaning (`/status`, the console card and the rc=5 guards want
the CONNACK). Assertions are on the ORDER OF EFFECTS, not source text.
"""
import io, os
from contextlib import redirect_stdout

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
pytest.importorskip("paho.mqtt.client")


class _OrderRecordingClient:
    """Records every subscribe and what stdout had said by then. Accepts one topic or the
    `[(topic, qos), …]` list the runtime sends (one SUBSCRIBE ⇒ one SUBACK)."""

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
# THE SECOND HANDSHAKE: the line a harness may actually boot a robot on (the SUBACK).
# --------------------------------------------------------------------------- #
SUBACK_LINE = "[runtime] subscriptions acknowledged by the broker"


def test_the_subscribed_line_is_not_printed_by_the_connack():
    """The bug in one assertion: `_on_connect` has only ASKED, so it must not print that the
    broker answered — the SUBSCRIBE is still queued."""
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
    """No counting in `_on_subscribe`: one list subscribe is one packet and one ack (four
    calls would be four SUBACKs, and a flag on the first is the original bug)."""
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
    """A SUBACK is a fact about a socket: after a reconnect (clean sessions re-subscribe),
    readiness must not be claimed by the previous connection's ack."""
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
    """A SUBACK can refuse (`0x80` per filter, e.g. an ACL not granting `/devices/+/state`);
    the supervisor is then deaf, so readiness must not arm."""
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
    """Exactly one `.subscribe(` call in the runtime — the fact ack-without-counting rests on
    (a second call would let an unrelated ack arm readiness early)."""
    from helpers_runtime import runtime_source
    src = runtime_source()
    calls = [ln.strip() for ln in src.splitlines()
             if ".subscribe(" in ln and not ln.strip().startswith("#")]
    assert calls == ["c.subscribe([(t, 0) for t in self.SUBSCRIPTIONS])"], (
        f"the runtime subscribes in more than one place: {calls}. Either fold it into "
        f"`_on_connect`'s single list call, or make `_on_subscribe` match the mid it is "
        f"waiting for — an unmatched SUBACK arming readiness is the bug again.")
