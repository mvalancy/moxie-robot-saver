"""
🔌 The broker connection's own durable history — the shapes, and the runtime wiring.

Build document:
[`docs/architecture/backlog/production-hardening.md`](../../docs/architecture/backlog/production-hardening.md)
**§8 P1** — *"a connection telemetry stream (connects, disconnects, CONNACK reason codes,
gap durations, dropped publishes) on the existing `JsonStore` telemetry shape."*

What P0 left, and why it is not enough. P0 put six fields on `/status`, and every one is a
**scalar in one process's RAM**: `last_broker_disconnect` + `last_broker_connect` describe
the *most recent* gap and forget every earlier one, `publish_drops` is a count with no
trend, and a restart — usually the event you wanted to read about — erases all six. So the
question this file's subject exists to answer is the one an operator actually has: *"it is
up now; how many times has it not been, and for how long?"*

Hermetic: no broker, no network, no sleeping. Every test that needs a store gets its own
`tmp_path` one, because the session-wide `MOXIE_DATA_DIR` fixture is shared and a
fleet-tier ring is exactly the kind of record that would otherwise accumulate across the
whole suite.

**No wall clock is read here** (`test_clock_dependence.py`'s ratchet): every timestamp and
every gap is an injected number, which is also the only way to assert a gap *value* rather
than a range.
"""
from __future__ import annotations

import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
sys.path.insert(0, os.path.dirname(__file__))

from helpers_runtime import make_runtime                          # noqa: E402
from moxie_sdk import conn_telemetry as conn                      # noqa: E402
from moxie_sdk.app import MoxieApp                                # noqa: E402
from moxie_sdk.store import JsonStore                             # noqa: E402
from moxie_sdk.types import Reply                                 # noqa: E402

T0 = 1_800_000_000        # a fixed instant, well past the module's epoch floor


class EchoApp(MoxieApp):
    name = "test-conn"

    def respond(self, turn):
        return Reply(text=f"You said: {turn.speech}")


def _rt(tmp_path, **kw):
    """A runtime whose store is its own — so one test's ring is never another's."""
    return make_runtime(EchoApp(), store=JsonStore(str(tmp_path)), **kw)


# --------------------------------------------------------------------------- #
# The shapes
# --------------------------------------------------------------------------- #

def test_a_row_carries_only_the_fields_its_kind_means():
    """An absent key is an answer. `gap_s: 0.0` on a first connect would be
    indistinguishable from a zero-second outage, so the key is simply not there."""
    first = conn.build_event(conn.CONNECT, at=T0)
    assert first == {"kind": "connect", "at": T0}
    assert "gap_s" not in first and "device_id" not in first

    later = conn.build_event(conn.CONNECT, at=T0 + 60, gap_s=4.25)
    assert later["gap_s"] == 4.25

    drop = conn.build_event(conn.PUBLISH_DROP, at=T0, device_id="d_1",
                            topic="/devices/d_1/commands/remote_chat", reason="no socket")
    assert drop["device_id"] == "d_1" and drop["topic"].endswith("remote_chat")
    assert drop["reason"] == "no socket"


def test_a_reason_off_the_wire_is_truncated_not_trusted():
    """`reason` is `connack_string(rc)` today, but `rc` arrives from the broker. A row is
    a bounded thing in a bounded ring; an unbounded one from the wire would be the ring's
    cap quietly not applying."""
    row = conn.build_event(conn.REFUSED, at=T0, reason="x" * 5000)
    assert len(row["reason"]) == conn.MAX_REASON_CHARS


def test_a_timestamp_before_the_epoch_floor_is_refused():
    """A row stamped 1970 sorts to the front of the ring forever. Both the nonsense value
    and an unparseable one fall back to now — which is the only honest guess — and `now`
    is necessarily ≥ the floor, so the assertion needs no clock of its own."""
    for bad in (0, -1, 12345, "not a time", None):
        assert conn.build_event(conn.CONNECT, at=bad)["at"] >= conn._EPOCH_FLOOR


def test_a_negative_duration_is_clamped_rather_than_stored():
    """The realistic way to get one is NTP stepping the appliance's clock backwards at
    boot. A negative gap in a roll-up poisons every average computed from it."""
    assert conn.build_event(conn.CONNECT, at=T0, gap_s=-30.0)["gap_s"] == 0.0
    assert conn.gap_since(T0 + 10, now=T0) == 0.0


def test_the_first_connect_has_no_gap_at_all():
    """None, not zero — the distinction the whole `gap_s`-is-absent design rests on."""
    assert conn.gap_since(0.0, now=T0) is None
    assert conn.gap_since(T0 - 5.0, now=T0) == 5.0


def test_summarize_counts_kinds_and_measures_only_real_gaps():
    events = [
        conn.build_event(conn.CONNECT, at=T0),                        # no gap: first
        conn.build_event(conn.DISCONNECT, at=T0 + 10, reason="lost"),
        conn.build_event(conn.CONNECT, at=T0 + 12, gap_s=2.0),
        conn.build_event(conn.DISCONNECT, at=T0 + 20, reason="lost"),
        conn.build_event(conn.CONNECT, at=T0 + 80, gap_s=60.0),
        conn.build_event(conn.PUBLISH_DROP, at=T0 + 21, device_id="d_1"),
    ]
    s = conn.summarize(events)
    assert s["count"] == 6
    assert s["by_kind"] == {"connect": 3, "disconnect": 2, "publish_drop": 1}
    # Two reconnects, not three connects: the first connect was not a recovery from
    # anything, and counting it would put a phantom outage in every appliance's history.
    assert s["gaps"]["count"] == 2
    assert s["gaps"]["total_s"] == 62.0
    assert s["gaps"]["max_s"] == 60.0
    assert s["first_at"] == T0 and s["last_at"] == T0 + 21


def test_an_appliance_that_never_dropped_reports_no_gaps_not_a_zero_average():
    s = conn.summarize([conn.build_event(conn.CONNECT, at=T0)])
    assert s["gaps"] == {"count": 0, "total_s": 0.0, "max_s": 0.0, "p95_s": 0.0}


def test_p95_is_a_rank_over_observed_gaps_never_an_invented_value():
    """§5.3's A3 bar is stated as a p95 over observed reconnects. Nearest-rank returns a
    gap that actually happened; interpolation would return one that did not."""
    events = [conn.build_event(conn.CONNECT, at=T0 + i, gap_s=float(i))
              for i in range(1, 21)]                    # gaps 1.0 … 20.0
    gaps = conn.summarize(events)["gaps"]
    assert gaps["count"] == 20
    assert gaps["p95_s"] == 19.0        # ceil(0.95 * 20) == 19th of 20, 1-indexed
    assert gaps["p95_s"] in {float(i) for i in range(1, 21)}
    assert gaps["max_s"] == 20.0


def test_summarize_tolerates_a_ring_of_rubbish():
    """A store file a person edited, a row from a future version. A history that raises is
    a history nobody can read at exactly the moment they need it."""
    s = conn.summarize([None, "nope", 7, {"kind": "connect", "at": T0},
                        {"gap_s": "not a number"}])
    assert s["count"] == 2 and s["by_kind"]["connect"] == 1
    assert s["by_kind"]["unknown"] == 1


def test_summarize_of_nothing_is_a_whole_answer():
    for empty in (None, [], "not a list"):
        s = conn.summarize(empty)
        assert s["count"] == 0 and s["latest"] == [] and s["first_at"] is None


def test_health_reads_the_recorded_state_not_the_newest_row():
    """A ring whose newest row is a `disconnect` is very often a **connected** appliance
    whose reconnect row has not landed yet. A card that derived the verdict from the rows
    would flicker "down" once per reconnect."""
    ring = [conn.build_event(conn.CONNECT, at=T0),
            conn.build_event(conn.DISCONNECT, at=T0 + 10)]
    s = conn.summarize(ring)
    assert conn.health(s, connected=True)["state"] == "recovered"
    assert conn.health(s, connected=False)["state"] == "down"
    quiet = conn.summarize([conn.build_event(conn.CONNECT, at=T0)])
    assert conn.health(quiet, connected=True)["state"] == "steady"


def test_health_counts_a_refusal_apart_from_an_outage():
    """The operator action is completely different: one is "the broker is down", the other
    is "your credential is wrong". A single "problems" number would hide that."""
    s = conn.summarize([conn.build_event(conn.REFUSED, at=T0, reason="not authorised"),
                        conn.build_event(conn.CONNECT_FAIL, at=T0 + 1)])
    h = conn.health(s, connected=False)
    assert h["refusals"] == 1 and h["outages"] == 1


def test_the_cap_is_an_env_knob_and_a_bad_one_does_not_disable_it(monkeypatch):
    monkeypatch.setenv("MOXIE_CONN_MAX_EVENTS", "12")
    assert conn.max_events() == 12
    monkeypatch.setenv("MOXIE_CONN_MAX_EVENTS", "banana")
    assert conn.max_events() == conn.MAX_EVENTS


# --------------------------------------------------------------------------- #
# The runtime wiring — every callback that knows something records it
# --------------------------------------------------------------------------- #

def test_a_connect_a_drop_and_a_reconnect_are_all_on_disk(tmp_path):
    """The sequence that six scalars cannot express: two outages, both still readable
    after the second recovery."""
    rt, device_id = _rt(tmp_path)
    rt.client.up()
    rt.client.drop()
    rt.client.up()
    rt.client.drop()
    rt.client.up()

    kinds = [e["kind"] for e in rt.conn_events()]
    assert kinds.count(conn.CONNECT) == 3
    assert kinds.count(conn.DISCONNECT) == 2
    # And they are in the order they happened — the property a stream has and a scalar
    # cannot: connect, disconnect, connect, disconnect, connect.
    assert kinds == [conn.CONNECT, conn.DISCONNECT, conn.CONNECT,
                     conn.DISCONNECT, conn.CONNECT]


def test_the_second_connect_carries_the_gap_and_the_first_does_not(tmp_path):
    rt, _ = _rt(tmp_path)
    rt.client.up()
    assert "gap_s" not in rt.conn_events()[0]
    # Move the recorded disconnect back by a known amount rather than sleeping: the gap is
    # computed from `last_broker_disconnect`, so an injected value gives an exact assertion
    # and reads no clock of its own.
    rt.client.drop()
    rt.last_broker_disconnect -= 7.0
    rt.client.up()
    reconnect = [e for e in rt.conn_events() if e["kind"] == conn.CONNECT][-1]
    assert reconnect["gap_s"] >= 7.0
    assert conn.summarize(rt.conn_events())["gaps"]["count"] == 1


def test_a_connack_refusal_is_recorded_as_a_refusal_not_a_connect(tmp_path):
    """The row that keeps C3 honest in the *history* as well as in the log: `rc=5` must
    never read as a connect after the fact either."""
    rt, _ = _rt(tmp_path)
    rt.client.refuse(rc=5)
    rows = rt.conn_events()
    assert [e["kind"] for e in rows] == [conn.REFUSED]
    assert rows[0]["reason"], "a refusal with no reason is the rc=5 log line all over again"
    assert conn.CONNECT not in [e["kind"] for e in rows]


def test_a_connect_fail_is_its_own_kind(tmp_path):
    """`on_connect_fail` (the socket never opened) is neither a refusal nor a disconnect,
    and without a distinct row the retry loop is invisible in the history the way it used
    to be invisible in the log."""
    rt, _ = _rt(tmp_path)
    rt._on_connect_fail(rt.client)
    assert [e["kind"] for e in rt.conn_events()] == [conn.CONNECT_FAIL]


def test_a_dropped_publish_is_recorded_with_the_robot_it_was_meant_for(tmp_path):
    rt, device_id = _rt(tmp_path)
    rt.client.up()
    rt.client.drop()
    ok, _ = rt._publish(f"/devices/{device_id}/commands/remote_chat", {"x": 1},
                        device_id=device_id)
    assert ok is False
    drops = [e for e in rt.conn_events() if e["kind"] == conn.PUBLISH_DROP]
    assert len(drops) == 1
    assert drops[0]["device_id"] == device_id
    assert drops[0]["topic"].endswith("remote_chat")
    # In the same ring as the disconnect that caused it, and after it — the ordering is
    # the reason this is one stream rather than a per-robot record.
    kinds = [e["kind"] for e in rt.conn_events()]
    assert kinds.index(conn.DISCONNECT) < kinds.index(conn.PUBLISH_DROP)


def test_a_store_lock_timeout_is_recorded_with_how_long_it_waited(tmp_path):
    """The row that measures A13. Without `waited_s` there is nothing to retune
    `MOXIE_STORE_LOCK_TIMEOUT_S` *from*, which is what §8's P1 line asks for."""
    rt, _ = _rt(tmp_path)
    rt._on_store_lock_timeout("/data/robots/d_1/memory.json.lock", 1.87)
    rows = [e for e in rt.conn_events() if e["kind"] == conn.LOCK_TIMEOUT]
    assert len(rows) == 1 and rows[0]["waited_s"] == 1.87
    assert conn.summarize(rt.conn_events())["lock_waits"]["max_s"] == 1.87


def test_the_recorder_never_recurses_into_itself(tmp_path):
    """The guard that is not paranoia: `_on_store_lock_timeout` records by **writing to
    the store**, and a store under contention is exactly when it fires. Without the flag,
    a refused conn-telemetry write calls the recorder that is already running.

    Proved by making the store's own append fire the timeout hook every time — the shape
    of a permanently contended record — and requiring the call to return rather than
    recurse. Without `_recording_conn` this is an unbounded recursion, not a slow test.
    """
    rt, _ = _rt(tmp_path)
    depth = {"max": 0, "now": 0}
    real_append = rt.store.append_shared

    def contended(collection, item, **kw):
        depth["now"] += 1
        depth["max"] = max(depth["max"], depth["now"])
        try:
            rt._on_store_lock_timeout("/x/y.lock", 2.0)     # the recorder, re-entered
            return real_append(collection, item, **kw)
        finally:
            depth["now"] -= 1

    rt.store.append_shared = contended
    rt._on_store_lock_timeout("/x/y.lock", 2.0)
    assert depth["max"] == 1, f"the recorder recursed {depth['max']} deep"


def test_a_broken_store_never_costs_a_turn(tmp_path):
    """`_record_conn` runs on the paho network thread and inside `_publish`. A telemetry
    write that raised there would take the MQTT loop down for a history nobody asked for."""
    rt, _ = _rt(tmp_path)

    def boom(*a, **kw):
        raise OSError("read-only /data")

    rt.store.append_shared = boom
    assert rt._record_conn(conn.CONNECT) is False        # says so, rather than pretending
    rt.client.up()                                        # and the callback still returns
    rt.client.drop()
    assert rt.broker_connected is False


def test_the_ring_is_capped_and_keeps_the_newest(monkeypatch, tmp_path):
    """§5.3 A9 — no state grows without bound. The cap is also the write cost: the store
    rewrites the whole file on every append."""
    monkeypatch.setenv("MOXIE_CONN_MAX_EVENTS", "5")
    rt, _ = _rt(tmp_path)
    for i in range(20):
        rt._record_conn(conn.CONNECT, at=T0 + i)
    rows = rt.conn_events()
    assert len(rows) == 5
    assert [e["at"] for e in rows] == [T0 + 15, T0 + 16, T0 + 17, T0 + 18, T0 + 19]


def test_conn_view_carries_both_the_live_scalars_and_the_history(tmp_path):
    """Both halves on purpose: an operator needs to tell "down right now" from "dropped
    nine times this hour and is up at the moment", and neither half answers alone."""
    rt, _ = _rt(tmp_path)
    rt.client.up()
    rt.client.drop()
    rt.client.up()
    view = rt.conn_view()
    assert view["ok"] is True
    assert view["connected"] is True                       # the live scalar
    assert view["summary"]["by_kind"][conn.DISCONNECT] == 1  # the history
    assert view["health"]["state"] == "recovered"
    assert view["retention"]["events"] == conn.max_events()
    assert view["events"], "the view must carry the rows, not only their counts"
    # Newest first, so a card renders it without re-sorting.
    ats = [e["at"] for e in view["events"]]
    assert ats == sorted(ats, reverse=True)


def test_status_carries_the_connection_health_headline(tmp_path):
    rt, _ = _rt(tmp_path)
    rt.client.up()
    snap = rt.status_snapshot()
    assert snap["connection_health"]["state"] == "steady"
    rt.client.drop()
    assert rt.status_snapshot()["connection_health"]["state"] == "down"


def test_the_history_survives_the_process_that_wrote_it(tmp_path):
    """The whole point. A second `JsonStore` over the same directory — which is what a
    restarted supervisor is — reads what the first one recorded."""
    rt, _ = _rt(tmp_path)
    rt.client.up()
    rt.client.drop()
    reborn = JsonStore(str(tmp_path))
    rows = reborn.read_shared(conn.COLLECTION, [])
    assert [e["kind"] for e in rows] == [conn.CONNECT, conn.DISCONNECT]


def test_no_child_data_is_ever_in_a_connection_row(tmp_path):
    """The asymmetry with `telemetry.py` stated as a test. `storable_packet` gates on
    `LoggingPolicy` because a Packet carries the child; these rows carry a topic, a device
    id, a reason code and a duration, so a `NO_DATA` appliance still gets its own health.
    A row that grew a payload field would silently make that untrue."""
    rt, device_id = _rt(tmp_path)
    rt.client.up()
    rt.client.drop()
    rt._publish(f"/devices/{device_id}/commands/remote_chat",
                {"speech": "my name is Robin and I live on Elm Street"},
                device_id=device_id)
    allowed = {"kind", "at", "reason", "device_id", "topic", "gap_s", "waited_s"}
    for row in rt.conn_events():
        assert set(row) <= allowed, f"unexpected field in a connection row: {row}"
        assert "Robin" not in repr(row) and "Elm" not in repr(row)


@pytest.mark.parametrize("kind", conn.KINDS)
def test_every_declared_kind_round_trips_through_the_store(kind, tmp_path):
    """A kind the module declares but the store cannot hold is a kind nobody will notice
    is missing until they go looking for it."""
    rt, _ = _rt(tmp_path)
    assert rt._record_conn(kind, at=T0) is True
    assert rt.conn_events()[-1]["kind"] == kind
