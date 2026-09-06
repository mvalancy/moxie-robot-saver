"""
The two telemetry records must not be able to disagree — constructed, not waited for.

WHAT THIS FILE IS ABOUT. Durable telemetry is deliberately **two records, not one**
(`moxie_sdk/telemetry.py`): a rolling ring of Packet envelopes for *"what just
happened"*, and a daily roll-up for *"what has been happening"*. The parent console's
📈 insights card reads the **roll-up** for its lifetime total and its week; it reads the
**ring** for the event list. So the two answering differently is not a cosmetic split —
it is the card stating a number confidently and wrongly, which is worse than a card that
states nothing.

WHY IT EXISTS. On 2026-09-05 the `sil` job went red on a PR whose diff could not reach
this code (PR #164 — mutation-check runners and test files only), with::

    assert [p["event_name"] for p in ring] == list(EVENTS)   # PASSED, all 3
    assert daily and daily["total"] == 3, daily
    E  AssertionError: {'days': {...'count': 2...}, 'total': 2, ...}

Three packets in the ring on disk, two counted in the roll-up on disk. The playbook rule
this repo records for exactly that shape (`orchestration-plan.md` §Integration playbook)
is that a check which reddens on a diff that cannot reach it is telling you about a real
defect that has been shipping, and that re-running is how it stays hidden.

THE MECHANISM, which is not a mystery once the two writes are put side by side.
`_persist_telemetry` used to do this, in this order::

    self.store.append(device_id, PACKETS_COLLECTION, row, cap=…)     # 1
    self.store.write(device_id, DAILY_COLLECTION, roll_up_packet(…)) # 2

Two files, two `os.replace` calls, no relationship between them. The SIL fixture's
leading edge is the **ring** file (it waits for three envelopes there and then reads the
daily file), so any observer — the fixture, a parent refreshing the console, a supervisor
that is killed — that lands between (1) and (2) sees the ring hold a packet the roll-up
has never counted. On a loaded CI runner that window is wide enough to hit; here it is
1-in-many, which under the same rule makes it a race with a stable rate rather than a
flake. Worse than the red test: a **restart** in that window makes the disagreement
**permanent**, because the roll-up was only ever advanced incrementally and nothing ever
reconciled it against the ring again.

HOW THIS FILE REPRODUCES IT. Not by sending packets in a loop and hoping. Two
constructions, each driving one exact interleaving:

* `_OrderedStore` records the on-disk state of **both** collections after every single
  write the runtime makes, so the sequence of intermediate states is a value a test can
  assert over. The invariant is *"the ring never leads the roll-up"* — at no moment may
  the ring file hold more envelopes than the roll-up file has counted. That is precisely
  the assertion the SIL test makes at one arbitrary instant, made at **every** instant.
* `_LosingStore` drops one nominated write and lets every other one through — the state a
  `kill -9` between the two `os.replace` calls leaves behind. The test then throws the
  runtime away and builds a new one over the same data directory (the restart), and asks
  it what happened.

Both fail against the pre-fix runtime and pass against the fixed one; that negative
control was run and is recorded in the commit that introduced them.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
sys.path.insert(0, os.path.dirname(__file__))

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
# Both subclass `JsonStore` at `_write_path`, the single choke point every write in the
# store funnels through (`write` → `_locked_write` → `_write_path`, `append` →
# `_append_path` → `_write_path`). Subclassing there rather than monkeypatching `write`
# and `append` separately is what makes the instrument order-agnostic: it sees the writes
# in the order the runtime actually issues them, whatever that order is, which is the
# property under test rather than an assumption about it.


class _OrderedStore(JsonStore):
    """Records the on-disk state of both telemetry collections after every write.

    `states` is a list of `(collection, ring_length, rollup_total)` read **off disk**,
    appended once per successful write. A test asserts over the whole sequence, so the
    window between two writes is a value rather than something to be timed."""

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
    """Drops the writes a test nominates, and lets every other one through.

    `lose(collection)` makes the *next* write to that collection a no-op that still
    reports success — the store's own contract for a write that reached `os.replace` and
    then had the process killed before the next one started. It reports success on purpose:
    a caller that retried would be simulating a different failure than the one that
    produced the CI red."""

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
    """**The CI red, made deterministic.**

    The SIL fixture waits for three envelopes in `telemetry_packets.json` and then reads
    `telemetry_daily.json`. Whether that read finds 3 or 2 depends entirely on where it
    lands between the runtime's two writes — so this asserts the property the fixture
    depends on, over the *whole* sequence of intermediate on-disk states rather than at
    the one instant a loaded runner happens to schedule.

    Pre-fix this fails on the very first packet: the ring is written first, so the state
    right after it is `(telemetry_packets, ring=1, total=0)`."""
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
    """The same invariant said the other way round, because the sequence above could in
    principle be satisfied by writing nothing at all.

    Each packet must move the roll-up's total to N *before* the ring's length reaches N,
    so the recorded states contain, for every N, a moment where the roll-up already says
    N and the ring does not yet."""
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
    """**The defect that outlives the red test.**

    Construct the exact state a supervisor killed between the two writes leaves behind —
    three envelopes in the ring, two counted in the roll-up — then restart (a brand-new
    `MoxieRuntime` over the same data directory, with an ordinary store) and ask it what
    happened. The ring is the durable log; the roll-up is a view over it, so the third
    packet's contribution has to be recoverable rather than gone.

    Pre-fix the new runtime answers 2 and always will: nothing ever looked at the two
    records together again."""
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
    """The migration case, and the one where guessing is expensive. An appliance
    upgrading into this fix has a ring of envelopes with no `seq` and a roll-up that
    already counted every one of them. Treating an unstamped row as *unfolded* would
    double the lifetime total of every existing installation on its first read, so an
    unstamped row is treated as folded — the only direction in which being wrong is
    invisible rather than alarming."""
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
    """The other half of the pair, and the reason `next_seq` consults both records.

    If the *ring* append is the write that is lost, the roll-up's watermark is briefly
    ahead of anything on disk. Deriving the next sequence from the ring alone would hand
    the following packet a number the roll-up has already marked as counted, and the
    repair would then skip a packet that really is missing. Monotonic beats gapless."""
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
    """The divergence that needs no crash at all.

    The ring's `append` is a read-modify-write **inside** `transaction()`; the roll-up's
    write was a read-modify-write with nothing around it, so two ingests landing together
    both read the same roll-up and the second overwrote the first's count — the ring
    keeping both and the roll-up keeping one. Telemetry arrives on the paho callback
    thread today, which is why this was latent rather than constant; the store's own
    docstring is explicit that two supervisors may share one data directory, and a worker
    pool is one refactor away."""
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
