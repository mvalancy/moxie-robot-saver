"""
S1–S8 — the supervisor stops dying when the broker is late, and stops lying when the
socket is dead.

Build document:
[`docs/architecture/backlog/production-hardening.md`](../../docs/architecture/backlog/production-hardening.md)
§4 and §6. Written before the fix and watched fail on `origin/dev` at 341965d, where the
three defects were:

1. **`_on_connect` never checked `rc`.** It printed `broker connected rc={rc}` and
   subscribed regardless, so a CONNACK refusal logged the words *"broker connected"* and
   then subscribed into a socket the broker was closing (**S4**).
2. **The wakeup route reported success into a dead socket.** Its guard was
   `self.client is None` — object existence, not `is_connected()` — and the `publish()`
   result was ignored, so a live client object over a dead socket answered
   `{"published": true}`. PR #55 shipped specifically to stop the console reporting
   success for nothing; this survived in the one place that fix did not look (**S1**).
3. **`connect_async` alone is a no-op under `loop_forever()`.** paho re-raises the first
   `OSError` unless `retry_first_connection=True` — the trap **S6** exists to catch, and
   the reason S6 must fail on a *half-done* fix, not only on no fix at all.

Hermetic: no broker, no network beyond one connection refused on loopback, and no wall
clock read (`test_clock_dependence.py`). S5 drives paho's own `_reconnect_wait` with the
client parked in a state that skips its sleep, so the delay ladder is *read* rather than
waited out.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))

from helpers_runtime import (FakeClient, LatchClient, free_port,   # noqa: E402
                             http_json, make_runtime, status_server)
from moxie_sdk.app import MoxieApp                                # noqa: E402
from moxie_sdk.types import Reply, RobotContext                   # noqa: E402
import moxie_runtime                                              # noqa: E402

CHAT = "/devices/{d}/commands/remote_chat"
CONFIG = "/devices/{d}/config"
WAKEUP = "/devices/{d}/commands/wakeup"


class EchoApp(MoxieApp):
    name = "test-resilience"

    def respond(self, turn):
        return Reply(text=f"You said: {turn.speech}")


class GateApp(MoxieApp):
    """An app that parks inside `respond` until a test lets it out — how a turn is held
    open across a disconnect without anybody sleeping."""
    name = "test-gate"

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def respond(self, turn):
        self.entered.set()
        assert self.release.wait(30), "the test never released the gated turn"
        return Reply(text=f"late answer to {turn.speech}")


# --------------------------------------------------------------------------- #
# S1 — nothing claims a publish succeeded when the socket was down (C5)
# --------------------------------------------------------------------------- #

def test_s1_a_publish_during_a_drop_is_not_ok_and_is_recorded(tmp_path):
    """S1 — every `publish()` while the broker is away must report failure and leave a
    record. paho at QoS 0 calls `_send_publish` directly and returns `MQTT_ERR_NO_CONN`
    when there is no socket; the message is **not** queued (A3). So a reply published
    during a gap is discarded, and before this fix *nothing in the process knew*.
    """
    rt, device_id = make_runtime(EchoApp())
    rt.client.up()
    ok, reason = rt._publish(CHAT.format(d=device_id), {"hello": 1}, device_id=device_id)
    assert ok is True and reason == ""

    rt.client.drop()
    ok, reason = rt._publish(CHAT.format(d=device_id), {"hello": 2}, device_id=device_id)
    assert ok is False
    # Which guard refused matters, not just that something did. This one is the
    # **pre-flight** check — `is_connected()`, not `client is not None` — so asserting the
    # exact sentence is what keeps that check load-bearing rather than shadowed by the
    # return-code check behind it. (Found by sim/tools/hardening_mutation_check.py: with
    # only `assert ok is False`, reverting `_broker_connected()` to object existence went
    # UNCAUGHT, because the rc check quietly covered for it.)
    assert reason == rt.NO_BROKER_REASON, reason
    assert rt.publish_drops == 1
    dropped = [n for n in rt.recent if n["kind"] == "drop"]
    assert dropped, f"the drop was not recorded in `recent`: {list(rt.recent)}"
    assert device_id in dropped[-1]["text"]


def test_s1d_a_transport_that_says_it_is_connected_and_is_not(tmp_path):
    """The second half of C5, and the half a pre-flight check cannot cover: the socket
    dies **between** `is_connected()` and the write.

    paho answers that with `info.rc = MQTT_ERR_NO_CONN` and drops the message — at QoS 0
    it is not queued (A3) — so the only way to know is to read the code back. All eight
    sites threw it away. Two independent guards, and this test exists so each is proved
    on its own rather than shadowing the other.
    """
    from helpers_runtime import FakeInfo, MQTT_ERR_NO_CONN

    class LyingClient(FakeClient):
        """`is_connected()` says yes; the write says otherwise."""

        def publish(self, topic, payload):
            self.dropped.append((topic, payload))
            return FakeInfo(MQTT_ERR_NO_CONN)

        def is_connected(self):
            return True

    rt, device_id = make_runtime(EchoApp())
    rt.client = LyingClient(runtime=rt)
    ok, reason = rt._publish(CHAT.format(d=device_id), {"hi": 1}, device_id=device_id)
    assert ok is False, "a publish that returned MQTT_ERR_NO_CONN was reported as sent"
    assert "rc=" in reason, reason
    assert rt.publish_drops == 1
    assert [n for n in rt.recent if n["kind"] == "drop"]

    # And the route on top of it stays honest — this is the wakeup button's real race.
    out = rt.wake_robot(device_id)
    assert out["ok"] is False and out["published"] is False, out
    assert out["error"] == "publish failed", out
    assert out["acknowledged"] is False


def test_s1e_a_transport_that_raises_is_a_drop_not_a_crash():
    """A transport that throws (a closed socket object, a broken pipe) must not take the
    turn down with it. The reply is lost either way; the difference is whether the
    supervisor knows."""
    class ThrowingClient(FakeClient):
        def publish(self, topic, payload):
            raise OSError("Broken pipe")

    rt, device_id = make_runtime(EchoApp())
    rt.client = ThrowingClient(runtime=rt)
    ok, reason = rt._publish(CHAT.format(d=device_id), {"hi": 1}, device_id=device_id)
    assert ok is False and "OSError" in reason
    assert rt.publish_drops == 1


def test_s1b_the_wakeup_route_refuses_instead_of_claiming_success(tmp_path):
    """S1, the part that matters most: the honesty contract of PR #55.

    `wake_robot`'s guard was `if self.client is None` — object existence, not connection —
    and it ignored the `publish()` result, so a live client object over a dead socket
    answered `{"ok": true, "published": true}`. The command has **no acknowledgement** in
    the recovered corpus, so `published` is the only true thing the route can say; saying
    it falsely is the whole bug. A refusal must carry a reason a parent can act on.
    """
    rt, device_id = make_runtime(EchoApp())
    rt.client.up()
    out = rt.wake_robot(device_id)
    assert out["ok"] is True and out["published"] is True
    assert out["acknowledged"] is False, "never a claim that the robot woke"
    assert rt.client.on(WAKEUP.format(d=device_id))

    rt.client.drop()
    out = rt.wake_robot(device_id)
    assert out["ok"] is False, out
    assert out["published"] is False, "reported a publish into a dead socket"
    assert out.get("acknowledged") is not True
    assert out["reason"] == rt.NO_BROKER_REASON, out
    # `error` says WHICH guard refused, and that is deliberate: this route must refuse
    # before it writes, on the connection rather than on the client object's existence.
    # (Without this line the mutation check found that reverting the guard to
    # `if self.client is None` went uncaught — `_publish` refused a moment later and the
    # answer looked the same.)
    assert out["error"] == "no broker connection", out
    assert len(rt.client.on(WAKEUP.format(d=device_id))) == 1, "it published anyway"


def test_s1c_every_publish_call_site_goes_through_the_helper():
    """The eight sites of §4.1 C5. A helper nobody calls fixes nothing, and the wakeup
    route is the standing proof that one forgotten site is enough — so this is asserted
    over the source rather than trusted.

    Comment lines are ignored: *citing* a wire shape is the house style here, and a guard
    that fires on a comment is playbook rule 17's lesson.
    """
    import ast
    path = os.path.join(REPO, "mqtt", "supervisor", "moxie_runtime.py")
    tree = ast.parse(open(path).read())

    def calls(node, dotted):
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            f = sub.func
            if (isinstance(f, ast.Attribute) and f.attr == dotted[-1]
                    and isinstance(f.value, ast.Attribute) and f.value.attr == dotted[-2]
                    and isinstance(f.value.value, ast.Name) and f.value.value.id == "self"):
                yield sub

    helper = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_publish")
    inside = {c.lineno for c in calls(helper, ("client", "publish"))}
    everywhere = {c.lineno for c in calls(tree, ("client", "publish"))}
    assert len(inside) == 1, "the helper must publish exactly once"
    stragglers = sorted(everywhere - inside)
    assert stragglers == [], (
        f"publish() call sites at lines {stragglers} still bypass `_publish()` and "
        f"ignore their return code — the bug PR #55 shipped to kill, in the places it "
        f"did not look")

    routed = [c for c in ast.walk(tree)
              if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
              and c.func.attr == "_publish"
              and isinstance(c.func.value, ast.Name) and c.func.value.id == "self"]
    assert len(routed) >= 8, f"only {len(routed)} sites go through the helper; §4.1 C5 lists 8"


# --------------------------------------------------------------------------- #
# S2 — a turn that spanned a drop is never spoken afterwards (§4.2)
# --------------------------------------------------------------------------- #

def test_s2_a_turn_started_before_a_drop_never_publishes_after_the_reconnect():
    """S2 — the turn is **abandoned**, marked stale by the mechanism the runtime already
    trusts, and never replayed.

    Replay is actively harmful under the recovered contract: one `event_id` may be
    answered by several chunks, and a chunk delivered after a 40 s gap lands on a robot
    that re-prompted at ~20 s with a *new* `event_id` — so the child would hear the answer
    to the question they gave up on, after the answer to the one they asked instead. That
    is exactly what `_is_stale` was written to prevent.

    The client is brought back **up** before the worker is released, so a runtime that had
    merely dropped the publish (rather than recognised the turn as stale) would succeed
    here. That is the difference this test exists to see.
    """
    app = GateApp()
    rt, device_id = make_runtime(app)
    rt.brain_budget_s = 0          # no filler: this test is about the ANSWER, not latency
    rt.streaming = False
    rt.client.up()
    robot = rt.robots[device_id]
    rt._on_remote_chat(device_id, robot, json.dumps(
        dict(command="prompt", backend="router", event_id="evt-1", speech="hello")))
    assert app.entered.wait(30), "the turn never reached the app"

    before = rt._turn_seq[device_id]
    rt.client.drop()
    assert rt._turn_seq[device_id] > before, "the drop did not stale the in-flight turn"

    rt.client.up()                       # the socket is fine again — and still no answer
    app.release.set()
    rt._pool.shutdown(wait=True)
    assert rt.client.chat_replies(device_id) == [], \
        "an answer to a turn the child abandoned was published after the reconnect"
    assert any(n["kind"] in ("drop", "conn") for n in rt.recent), list(rt.recent)


def test_s8_a_drop_bumps_the_turn_sequence_for_every_known_robot():
    """S8 — and for every robot, not just the busy one. `_turn_seq`'s documented invariant
    (`moxie_runtime.py`:2108-2110) is *"the MQTT loop is the only writer here, so a plain
    increment is enough"*, and `on_disconnect` is dispatched from the network loop — which
    under `loop_forever()` **is** that thread (A19). So the bump is free of new races, and
    this test pins both halves: every robot, and no new writer."""
    rt, device_id = make_runtime(EchoApp())
    for extra in ("d_two", "d_three"):
        rt.robots[extra] = RobotContext(device_id=extra, child=rt.child)
    rt._turn_seq.update({device_id: 4, "d_two": 1})       # d_three has never had a turn

    rt.client.up()
    rt.client.drop()
    assert rt._turn_seq[device_id] == 5
    assert rt._turn_seq["d_two"] == 2
    assert rt._turn_seq["d_three"] == 1, "a robot with no turn yet must still be staled"

    src = open(os.path.join(REPO, "mqtt", "supervisor", "moxie_runtime.py")).read()
    assert "_turn_seq[device_id] = self._turn_seq.get(device_id, 0) + 1" in src, \
        "the turn path's increment moved; re-read the single-writer invariant"


# --------------------------------------------------------------------------- #
# S3/S4 — CONNACK honesty (C3, C4)
# --------------------------------------------------------------------------- #

def test_s3_subscriptions_are_installed_once_per_successful_reconnect():
    """S3 — every successful CONNACK re-subscribes (paho drops subscriptions with the
    session), and nothing else does."""
    rt, _device_id = make_runtime(EchoApp())
    assert rt.client.subscribed == []

    rt.client.up()
    first = list(rt.client.subscribed)
    assert first, "a successful connect subscribed to nothing"
    assert any(t.startswith("$SYS/broker/log") for t in first)
    assert any("/devices/" in t for t in first)

    rt.client.drop()
    assert rt.client.subscribed == first, "a disconnect subscribed to something"

    rt.client.up()
    assert rt.client.subscribed == first + first, \
        "a reconnect did not re-install exactly the same subscriptions"


def test_s4_a_connack_refusal_subscribes_nothing_and_says_what_happened(capsys):
    """S4 — the *"broker connected rc=5"* line, killed.

    `rc=5` (*not authorised*) became reachable for the first time when PR #44 gave the
    supervisor a broker credential. `_on_connect` printed **"broker connected"** and then
    subscribed into a socket the broker was closing: a comfortable lie in the one place an
    operator looks. Ports Fork A's `moxie_server.py`:206-215 behaviour (MIT, © Justin
    Beghtol — behaviour only, no code).
    """
    rt, _device_id = make_runtime(EchoApp())
    rt.client.refuse(rc=5)

    assert rt.client.subscribed == [], "subscribed into a refused connection"
    assert rt.broker_connected is False
    assert rt.last_connect_error, "a refusal left no reason behind"
    assert "not authorised" in rt.last_connect_error.lower() \
        or "not authorized" in rt.last_connect_error.lower(), rt.last_connect_error

    out = capsys.readouterr().out
    assert "broker connected" not in out.lower(), \
        f"a CONNACK refusal still logged the words 'broker connected': {out!r}"
    assert any(n["kind"] == "error" for n in rt.recent), list(rt.recent)

    rt.client.up()                                     # and a real connect still works
    assert rt.broker_connected is True
    assert rt.client.subscribed


def test_s4b_the_status_endpoint_reports_the_connection_it_really_has():
    """C4 + file 8 of §8: `broker_connected`, `last_broker_connect`,
    `last_broker_disconnect`, `last_connect_error` on `/status`, so the console's existing
    connection monitor renders them with **no console change**."""
    rt, _device_id = make_runtime(EchoApp())
    base = status_server(rt)

    fresh = http_json(base + "/status")
    assert fresh["broker_connected"] is False
    assert fresh["last_broker_connect"] in (None, 0)

    rt.client.up()
    up = http_json(base + "/status")
    assert up["broker_connected"] is True
    assert up["last_broker_connect"]
    assert up["last_connect_error"] == ""

    rt.client.drop()
    down = http_json(base + "/status")
    assert down["broker_connected"] is False
    assert down["last_broker_disconnect"] >= up["last_broker_connect"]


def test_s4c_a_failed_connect_attempt_is_visible(capsys):
    """`on_connect_fail` — the socket never opened at all (broker down, DNS gone), which
    is a different event from a CONNACK refusal and from a disconnect. Without it the
    retry loop C1 adds is invisible, and *"it is just sitting there"* is the bug report."""
    rt, _device_id = make_runtime(EchoApp())
    rt._on_connect_fail(rt.client, None)
    assert rt.broker_connected is False
    assert rt.last_connect_error
    assert any(n["kind"] == "error" for n in rt.recent), list(rt.recent)
    # ...on **stdout** as well, not only in `recent`. Found live: a real supervisor
    # started before a real broker retried four times, recorded all four, and printed
    # nothing — so `docker logs` showed a process that said "connecting to broker" and
    # then went quiet, which reads exactly like the hang this change removes.
    assert "retrying" in capsys.readouterr().out

    # ...and it is actually installed on the real client. A callback nothing calls is the
    # same silence it was written to remove, and neither this test nor S6 would notice —
    # S6 installs its own counter on top. (Hole found by hardening_mutation_check.py.)
    fresh = moxie_runtime.MoxieRuntime(app=EchoApp())
    client = fresh._build_client()
    assert client.on_connect_fail == fresh._on_connect_fail
    assert client.on_disconnect == fresh._on_disconnect
    assert client.on_connect == fresh._on_connect


# --------------------------------------------------------------------------- #
# S5/S6 — the connect itself (C1, C2)
# --------------------------------------------------------------------------- #

def test_s5_the_reconnect_delay_ladder_is_1_2_4_capped_at_60():
    """S5 — `reconnect_delay_set(min_delay=1, max_delay=60)`.

    60 rather than paho's 120 because a house's router reboot is ~30-60 s and a 120 s
    ceiling is up to two minutes of a child talking to nothing; rather than Fork A's 30
    because we would rather not hammer a broker that has been down for an hour. **Chosen,
    not measured** (A14) — P1's gap telemetry is what settles it.

    The ladder is paho's own `_reconnect_wait`, driven here with the client parked in
    `DISCONNECTED` so its sleep loop is skipped entirely: the sequence is read, not waited
    out, and no clock is touched.
    """
    import paho.mqtt.client as mqtt
    rt = moxie_runtime.MoxieRuntime(app=EchoApp())
    client = rt._build_client()
    assert client._reconnect_min_delay == 1
    assert client._reconnect_max_delay == 60, (
        f"the reconnect ceiling is {client._reconnect_max_delay}s — paho's default is 120 "
        f"and C1 chose 60")

    client._state = mqtt._ConnectionState.MQTT_CS_DISCONNECTED    # skip the sleep
    ladder = []
    for _ in range(12):
        client._reconnect_wait()
        ladder.append(client._reconnect_delay)
    assert ladder[:7] == [1, 2, 4, 8, 16, 32, 60], ladder
    assert set(ladder[6:]) == {60}, ladder


def test_s2b_the_keepalive_is_thirty_and_is_a_choice():
    """C2 — `connect(host, port, 30)`'s third argument is the **keepalive**, not a timeout
    (A1, correcting the audit's implicit reading). 30 s → the broker declares us dead at
    45 s and paho notices a missing PINGRESP within one keepalive, which halves the
    worst-case detection of the half-open socket a NAT or a Wi-Fi drop actually produces.
    Kept deliberately, and now a named number instead of a literal nobody chose."""
    assert moxie_runtime.KEEPALIVE_S == 30
    src = open(os.path.join(REPO, "mqtt", "supervisor", "moxie_runtime.py")).read()
    assert "self.client.connect(self.host, self.port, 30)" not in src


def test_s6_a_supervisor_started_with_no_broker_retries_instead_of_dying():
    """S6 — **the test that catches `connect_async` without `retry_first_connection`.**

    `loop_forever()`'s default is `retry_first_connection=False`, and its first block
    re-raises the `OSError` from the initial `reconnect()` unless that flag is set (A2,
    proven by reading the installed paho). `loop_start()` gets it right only by accident —
    its thread body passes the flag — which is why Fork A's `connect_async` +
    `loop_start()` works and why porting *"add `connect_async`"* onto our `loop_forever()`
    changes **nothing**.

    So this test does not read the source: it runs the real `run()` against a port nothing
    is listening on and requires a *second* connection attempt. One attempt is what a
    blocking `connect()` does before it dies; two is a retry loop.
    """
    dead = free_port()                       # bound, read, and released — nothing listens
    with socket.socket() as probe:
        probe.settimeout(2)
        with pytest.raises(OSError):
            probe.connect(("127.0.0.1", dead))

    rt = moxie_runtime.MoxieRuntime(app=EchoApp(), host="127.0.0.1", port=dead)
    attempts = threading.Semaphore(0)
    rt._build_client()
    real_fail = rt._on_connect_fail

    def counting(c, u):
        real_fail(c, u)
        attempts.release()

    rt.client.on_connect_fail = counting
    crashed = []
    thread = threading.Thread(
        target=lambda: crashed.append(_run_and_capture(rt, free_port())), daemon=True)
    thread.start()
    try:
        assert attempts.acquire(timeout=30), "the supervisor never even tried to connect"
        assert attempts.acquire(timeout=30), (
            "the supervisor tried exactly once and then stopped: `connect_async` without "
            "`retry_first_connection=True` is a no-op under loop_forever() (A2)")
        assert thread.is_alive(), f"run() died instead of retrying: {crashed}"
    finally:
        rt.client._thread_terminate = True
        rt.client.disconnect()
        thread.join(timeout=10)


def _run_and_capture(rt, status_port):
    try:
        rt.run(status_port=status_port)
        return "returned"
    except BaseException as e:               # today: ConnectionRefusedError, immediately
        return f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------- #
# S7 — the two ingress paths, made symmetric (C6)
# --------------------------------------------------------------------------- #

def test_s7_an_event_from_an_unregistered_device_registers_it_and_pushes_config():
    """S7 — after a supervisor restart with the robot still connected, `$SYS/broker/log`
    has nothing to replay (A15: mosquitto publishes log lines live and never re-sends
    them) and a real Moxie publishes `/state` only on *its* connect, which we missed.

    `_on_state` has had the documented fallback since forever (*"fallback if we missed the
    log line"*); `_on_event` did not — it built an **ephemeral** `RobotContext` and
    answered the turn without ever registering the robot. So the appliance answered a
    robot it did not know it had: no config push, no `app.on_connect`, no presence state,
    invisible in `/status`, potentially for the rest of the session.
    """
    rt, _known = make_runtime(EchoApp(), allow_unverified_bots=True)
    rt.client = LatchClient()
    rt.client.runtime = rt
    rt.client.up()

    stranger = "d_after_restart"
    assert stranger not in rt.robots
    rt._on_event(stranger, "remote-chat", json.dumps(
        dict(command="prompt", backend="router", event_id="e1", speech="hi")))

    assert stranger in rt.robots, "the turn was answered from an ephemeral context"
    assert rt.client.wait_for(
        lambda pub: any(t == CONFIG.format(d=stranger) for t, _p in pub), timeout=20), \
        "the re-registered robot was never sent its config"
    rt._pool.shutdown(wait=True)

    cfg = rt.client.on(CONFIG.format(d=stranger))[-1]
    assert cfg.get("pairing_status"), cfg
    assert rt.client.chat_replies(stranger), "the turn itself must still be answered"


def test_s7b_the_two_ingress_paths_agree():
    """The point of C6 is *symmetry*: `_on_state` and `_on_event` must reach the same
    registration. Asserted as behaviour on both paths rather than as a shape."""
    rt, _known = make_runtime(EchoApp())
    rt.client.up()
    rt._on_state("d_via_state", b"{}")
    assert "d_via_state" in rt.robots
    rt._on_event("d_via_event", "client-service-activity-log",
                 json.dumps({"subtopic": "query", "query": "schedule"}))
    assert "d_via_event" in rt.robots
    rt._pool.shutdown(wait=True)


def test_s7c_an_unpermitted_stranger_is_still_refused():
    """C6 must not become a way past the pairing gate. Registration makes a robot
    **visible as pending**; it does not let it in. The gate lives on the transport boundary
    (`_on_message`), so this asserts the gate is still the thing that decides."""
    rt = moxie_runtime.MoxieRuntime(app=EchoApp())           # default: closed policy
    rt.client = FakeClient()
    rt.client.runtime = rt
    rt.client.up()

    class _Msg:
        topic = "/devices/d_stranger/events/remote-chat"
        payload = json.dumps(dict(command="prompt", backend="router",
                                  event_id="e1", speech="hi")).encode()

    rt._on_message(rt.client, None, _Msg())
    rt._pool.shutdown(wait=True)
    assert not rt.is_permitted("d_stranger")
    replies = rt.client.chat_replies("d_stranger")
    assert replies, "an unpermitted robot still gets the not-paired line"
    assert rt.NOT_PAIRED_LINE.split(".")[0] in json.dumps(replies[-1])
