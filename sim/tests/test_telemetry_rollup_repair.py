"""
The two telemetry records must not be able to disagree — constructed, not waited for.

Durable telemetry is two records (`moxie_sdk/telemetry.py`): a ring of Packet envelopes
("what just happened") and a daily roll-up ("what has been happening"). The insights card
reads its totals from the roll-up and its list from the ring, so disagreement means a
confidently wrong number.

The ring and roll-up were written by two independent `os.replace` calls, ring first. Any
observer between them (a SIL fixture, a parent, a kill -9) saw the ring hold a packet the
roll-up never counted; after a restart in that window the disagreement was permanent,
since the roll-up was only ever advanced incrementally. (Seen as a CI red: ring 3, total 2.)

Reproduced by construction, not by looping:
* `_OrderedStore` records both collections' on-disk state after EVERY write, so "the ring
  never leads the roll-up" is asserted at every instant, not one arbitrary one.
* `_LosingStore` drops one nominated write (the state a kill between the two writes
  leaves), then a new runtime over the same data dir is asked what happened.

Both failed against the pre-fix runtime.
"""
from __future__ import annotations

import json
import os

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

pytest.importorskip("paho.mqtt.client", reason="the runtime needs paho")

from helpers_runtime import make_runtime            # noqa: E402
from moxie_sdk import telemetry as T                # noqa: E402
from moxie_sdk.app import MoxieApp                  # noqa: E402
from moxie_sdk.store import JsonStore               # noqa: E402


class _App(MoxieApp):
    name = "content"


EVENTS = ("module_started", "module_finished", "battery_report")


# --------------------------------------------------------------------------- #
# The instruments
# --------------------------------------------------------------------------- #
# Both subclass `JsonStore._write_path`, the choke point every write funnels through, so
# they observe writes in whatever order the runtime issues them (the property under test).


class _OrderedStore(JsonStore):
    """Records `(collection, ring_length, rollup_total)`, read off disk, after every
    successful write — the window between writes becomes a value, not a timing."""

    def __init__(self, root, device_id):
        super().__init__(root)
        self.device_id = device_id
        self.states: list = []

    def _snapshot(self, collection):
        ring = self._read_path(self.path(self.device_id, T.PACKETS_COLLECTION), [])
        daily = self._read_path(self.path(self.device_id, T.DAILY_COLLECTION), {})
        self.states.append((collection,
                            len(ring) if isinstance(ring, list) else 0,
                            (daily or {}).get("total", 0)))

    def _write_path(self, path, value):
        ok = super()._write_path(path, value)
        if ok:
            self._snapshot(os.path.basename(path)[: -len(".json")])
        return ok


class _LosingStore(JsonStore):
    """`lose(collection)` makes the next write to it a no-op that still reports success —
    a write that "happened" before the process was killed (a retrying caller would
    simulate a different failure)."""

    def __init__(self, root):
        super().__init__(root)
        self.losing: set = set()
        self.lost: list = []

    def lose(self, collection):
        self.losing.add(collection)

    def _write_path(self, path, value):
        name = os.path.basename(path)[: -len(".json")]
        if name in self.losing:
            self.losing.discard(name)
            self.lost.append(name)
            return True
        return super()._write_path(path, value)


def _rt(tmp_path, store):
    return make_runtime(_App(), store=store)


def _send(rt, device_id, name):
    return rt.ingest_telemetry(device_id, json.dumps(
        T.build_packet(name, b"\x01\x02opaque", moxie_id=device_id)))


def _on_disk(tmp_path, device_id, collection, default):
    p = os.path.join(str(tmp_path), "robots", device_id, f"{collection}.json")
    if not os.path.exists(p):
        return default
    with open(p) as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# Construction 1 — the observation window, at every instant instead of one
# --------------------------------------------------------------------------- #

def test_the_ring_on_disk_never_leads_the_rollup_on_disk(tmp_path):
    """The CI red, deterministic: over the whole sequence of on-disk states, the ring never
    holds more envelopes than the roll-up has counted. Pre-fix, the first state is
    `(telemetry_packets, ring=1, total=0)`."""
    store = _OrderedStore(str(tmp_path), "d_test")
    rt, did = _rt(tmp_path, store)
    for name in EVENTS:
        _send(rt, did, name)

    assert store.states, "the instrument recorded no writes at all"
    leading = [s for s in store.states if s[1] > s[2]]
    assert not leading, (
        "the ring holds envelopes the roll-up has not counted; an observer whose leading "
        f"edge is the ring can read a roll-up that under-reports: {store.states}")

    # And the end state is the one the SIL test asserts.
    assert len(_on_disk(tmp_path, did, T.PACKETS_COLLECTION, [])) == 3
    assert _on_disk(tmp_path, did, T.DAILY_COLLECTION, {})["total"] == 3


def test_every_packet_is_counted_before_its_envelope_is_visible(tmp_path):
    """The same invariant from the other side (so writing nothing cannot satisfy it): for
    every N there is a moment where the roll-up says N and the ring does not yet."""
    store = _OrderedStore(str(tmp_path), "d_test")
    rt, did = _rt(tmp_path, store)
    for name in EVENTS:
        _send(rt, did, name)

    totals = [s[2] for s in store.states]
    assert totals == sorted(totals), f"the roll-up total went backwards: {store.states}"
    for n in (1, 2, 3):
        assert any(total >= n and ring < n for _, ring, total in store.states), (
            f"nothing counted packet {n} before its envelope reached the ring: "
            f"{store.states}")


# --------------------------------------------------------------------------- #
# Construction 2 — the restart that lands in the window
# --------------------------------------------------------------------------- #

def test_a_lost_rollup_write_is_repaired_from_the_ring_after_a_restart(tmp_path):
    """The defect that outlives the red: construct ring=3/roll-up=2 (killed between the
    writes), restart with an ordinary store, and the third packet must be recovered — the
    ring is the durable log, the roll-up a view over it."""
    store = _LosingStore(str(tmp_path))
    rt, did = _rt(tmp_path, store)
    _send(rt, did, EVENTS[0])
    _send(rt, did, EVENTS[1])
    store.lose(T.DAILY_COLLECTION)                 # the kill lands here
    _send(rt, did, EVENTS[2])
    assert store.lost == [T.DAILY_COLLECTION], store.lost

    # The constructed divergence, read straight off disk — the CI failure, by hand.
    assert len(_on_disk(tmp_path, did, T.PACKETS_COLLECTION, [])) == 3
    assert _on_disk(tmp_path, did, T.DAILY_COLLECTION, {})["total"] == 2

    fresh, _ = _rt(tmp_path, JsonStore(str(tmp_path)))          # the restart
    fresh.robots.pop(did, None)                                 # nothing to re-populate RAM
    view = fresh.telemetry_view(did)
    assert view["ok"] is True, view
    assert view["totals"]["total"] == 3, view["totals"]
    day = view["history"][-1]
    assert day["count"] == 3, day
    assert day["by_event"][EVENTS[2]] == 1, day


def test_the_repair_is_written_back_so_it_costs_nothing_twice(tmp_path):
    """A repair that lived only in the answer would be recomputed on every refresh and
    would vanish the moment the ring wrapped past the missing packet. The roll-up file
    itself must heal."""
    store = _LosingStore(str(tmp_path))
    rt, did = _rt(tmp_path, store)
    _send(rt, did, EVENTS[0])
    store.lose(T.DAILY_COLLECTION)
    _send(rt, did, EVENTS[1])
    assert _on_disk(tmp_path, did, T.DAILY_COLLECTION, {})["total"] == 1

    fresh, _ = _rt(tmp_path, JsonStore(str(tmp_path)))
    assert fresh.telemetry_rollup(did)["total"] == 2
    assert _on_disk(tmp_path, did, T.DAILY_COLLECTION, {})["total"] == 2, \
        "the repaired roll-up was not written back to disk"


def test_the_repair_never_double_counts_a_packet_it_already_folded(tmp_path):
    """The dangerous direction. Under-reporting is a wrong number; over-reporting is a
    wrong number that *grows every time a parent opens the card*, because a repair that
    cannot tell folded from unfolded re-folds the whole ring on each read."""
    rt, did = _rt(tmp_path, JsonStore(str(tmp_path)))
    for name in EVENTS:
        _send(rt, did, name)
    for _ in range(5):
        assert rt.telemetry_rollup(did)["total"] == 3
        assert rt.telemetry_view(did)["totals"]["total"] == 3
    assert _on_disk(tmp_path, did, T.DAILY_COLLECTION, {})["total"] == 3


def test_an_ingest_after_the_loss_repairs_as_well_as_appends(tmp_path):
    """The supervisor need not be restarted for the roll-up to heal: the next packet's
    own read of the roll-up goes through the same reconcile, so a robot that keeps
    talking fixes the number without anybody looking at the card."""
    store = _LosingStore(str(tmp_path))
    rt, did = _rt(tmp_path, store)
    _send(rt, did, EVENTS[0])
    store.lose(T.DAILY_COLLECTION)
    _send(rt, did, EVENTS[1])
    assert _on_disk(tmp_path, did, T.DAILY_COLLECTION, {})["total"] == 1
    _send(rt, did, EVENTS[2])
    assert _on_disk(tmp_path, did, T.DAILY_COLLECTION, {})["total"] == 3
    assert len(_on_disk(tmp_path, did, T.PACKETS_COLLECTION, [])) == 3


# --------------------------------------------------------------------------- #
# The seam the repair is built on
# --------------------------------------------------------------------------- #

def test_a_stored_envelope_carries_the_sequence_the_repair_reads(tmp_path):
    """`seq` is what makes "already folded" a fact rather than a guess. It is stamped by
    the runtime **after** the privacy gate, so a robot cannot forge one: `storable_packet`
    keeps only `_PACKET_FIELDS`."""
    rt, did = _rt(tmp_path, JsonStore(str(tmp_path)))
    for name in EVENTS:
        _send(rt, did, name)
    ring = _on_disk(tmp_path, did, T.PACKETS_COLLECTION, [])
    assert [T.packet_seq(r) for r in ring] == [1, 2, 3]
    assert _on_disk(tmp_path, did, T.DAILY_COLLECTION, {})["through_seq"] == 3


def test_a_robot_cannot_forge_its_own_sequence_number(tmp_path):
    """A packet arriving with a `seq` of its own must not be able to move the watermark —
    that would let one malformed robot mark the whole ring as counted."""
    rt, did = _rt(tmp_path, JsonStore(str(tmp_path)))
    pkt = T.build_packet("wake", b"", moxie_id=did)
    pkt["seq"] = 9999
    rt.ingest_telemetry(did, json.dumps(pkt))
    ring = _on_disk(tmp_path, did, T.PACKETS_COLLECTION, [])
    assert [T.packet_seq(r) for r in ring] == [1]
    assert _on_disk(tmp_path, did, T.DAILY_COLLECTION, {})["through_seq"] == 1


def test_a_pre_seq_store_is_not_re_folded_on_upgrade(tmp_path):
    """Migration: legacy ring rows have no `seq` and were already counted, so an unstamped
    row is treated as FOLDED — the only direction where being wrong is invisible rather
    than doubling every installation's lifetime total."""
    did = "d_test"
    legacy_ring = [T.storable_packet(T.build_packet(n, b"", moxie_id=did), 1)
                   for n in EVENTS]
    rollup = T.new_rollup()
    for row in legacy_ring:
        rollup = T.roll_up_packet(rollup, row)
    rollup.pop("through_seq", None)                # written before the watermark existed
    store = JsonStore(str(tmp_path))
    store.write(did, T.PACKETS_COLLECTION, legacy_ring)
    store.write(did, T.DAILY_COLLECTION, rollup)

    rt, _ = _rt(tmp_path, JsonStore(str(tmp_path)))
    assert rt.telemetry_rollup(did)["total"] == 3
    # …and the next real packet still lands exactly once.
    _send(rt, did, "battery_report")
    assert rt.telemetry_rollup(did)["total"] == 4


def test_a_lost_ring_write_never_lets_the_next_packet_reuse_a_sequence(tmp_path):
    """If the RING append is the lost write, the roll-up watermark is briefly ahead; `next_seq`
    consults both records so the next packet is not given an already-counted number.
    Monotonic beats gapless."""
    store = _LosingStore(str(tmp_path))
    rt, did = _rt(tmp_path, store)
    store.lose(T.PACKETS_COLLECTION)
    _send(rt, did, EVENTS[0])                      # counted, never listed
    assert _on_disk(tmp_path, did, T.DAILY_COLLECTION, {})["through_seq"] == 1
    assert _on_disk(tmp_path, did, T.PACKETS_COLLECTION, []) == []

    _send(rt, did, EVENTS[1])
    ring = _on_disk(tmp_path, did, T.PACKETS_COLLECTION, [])
    assert [T.packet_seq(r) for r in ring] == [2], "a sequence number was reused"
    daily = _on_disk(tmp_path, did, T.DAILY_COLLECTION, {})
    assert daily["total"] == 2 and daily["through_seq"] == 2
    # And a read does not now re-fold the surviving envelope on top of itself.
    assert rt.telemetry_rollup(did)["total"] == 2


def test_two_ingests_at_once_do_not_lose_a_rollup_update(tmp_path):
    """No crash needed: the roll-up's read-modify-write must be inside a transaction too, or
    two concurrent ingests both read the same roll-up and one count is lost (latent while
    telemetry arrives on one paho thread; two supervisors may share a data dir)."""
    import threading

    store = JsonStore(str(tmp_path))
    rt, did = _rt(tmp_path, store)
    start = threading.Barrier(8)

    def ingest(i):
        start.wait(10)
        _send(rt, did, f"e{i}")

    threads = [threading.Thread(target=ingest, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert len(_on_disk(tmp_path, did, T.PACKETS_COLLECTION, [])) == 8
    daily = _on_disk(tmp_path, did, T.DAILY_COLLECTION, {})
    assert daily["total"] == 8, daily
    assert sorted(T.packet_seq(r)
                  for r in _on_disk(tmp_path, did, T.PACKETS_COLLECTION, [])) == \
        [1, 2, 3, 4, 5, 6, 7, 8]


def test_erasing_the_history_resets_the_watermark_too(tmp_path):
    """`erase_telemetry` removes both files, so the sequence restarts at 1 against a
    roll-up whose watermark is back to 0. A watermark that survived the erase would make
    every packet after it look already-counted."""
    rt, did = _rt(tmp_path, JsonStore(str(tmp_path)))
    for name in EVENTS:
        _send(rt, did, name)
    assert rt.erase_telemetry(did)["erased"] is True
    _send(rt, did, "wake")
    ring = _on_disk(tmp_path, did, T.PACKETS_COLLECTION, [])
    assert [T.packet_seq(r) for r in ring] == [1]
    assert rt.telemetry_rollup(did)["total"] == 1
