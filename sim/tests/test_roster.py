"""
🤖 The durable robot roster — the appliance stops waiting for an event that never comes.

Build document:
[`docs/architecture/backlog/production-hardening.md`](../../docs/architecture/backlog/production-hardening.md)
**§8 P1** — *"a durable robot roster (a 15th collection) so a restart re-pushes config to
every robot it has ever seen rather than waiting for an event."*

The hole, precisely (§2.2, A15). `MoxieRuntime.robots` is memory-only, and every way the
supervisor learns a robot is present is an **event**:

* the `$SYS/broker/log` connect line is published **live and never replayed** on
  re-subscribe;
* `/state` is something a real Moxie sends on **its own** connect, not on ours;
* P0's C6 registers an unknown device — but only when it next speaks.

So after a supervisor restart, with the robot still happily connected, there is no event
to wait for. C6 made that recovery *possible*. It did not make it *prompt*: the appliance
stays silent until the child does, which at bedtime is tomorrow.

**The property this file guards hardest is a negative one.** A rostered robot must NOT be
marked connected. Inventing presence to make `/status` look populated is precisely the
disease this brief was written about — a status field reporting a comfortable belief
instead of an observation — and it is the single easiest way to "improve" this feature
into a lie.

Hermetic: no broker, no network, no sleeping. The resume path is driven directly rather
than through its settle timer, so nothing here waits on wall clock.
"""
from __future__ import annotations

import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
sys.path.insert(0, os.path.dirname(__file__))

from helpers_runtime import make_runtime                          # noqa: E402
from moxie_sdk import roster as roster_seam                       # noqa: E402
from moxie_sdk.app import MoxieApp                                # noqa: E402
from moxie_sdk.store import JsonStore                             # noqa: E402
from moxie_sdk.types import Reply                                 # noqa: E402

T0 = 1_800_000_000
CONFIG = "/devices/{d}/config"


class EchoApp(MoxieApp):
    name = "test-roster"

    def respond(self, turn):
        return Reply(text=f"You said: {turn.speech}")


def _rt(tmp_path, **kw):
    return make_runtime(EchoApp(), store=JsonStore(str(tmp_path)), **kw)


# --------------------------------------------------------------------------- #
# The shapes
# --------------------------------------------------------------------------- #

def test_record_seen_never_mutates_the_roster_it_was_given():
    """Purity is not style here. The runtime does the read-modify-write inside
    `store.transaction_shared()`, so a refused lock must leave the in-memory value
    untouched rather than half-applied."""
    before = roster_seam.new_roster()
    after = roster_seam.record_seen(before, "d_1", at=T0)
    assert before == {"devices": {}}
    assert "d_1" in after["devices"]


def test_first_seen_survives_every_later_sighting():
    """*"Since when has this appliance served this robot"* and *"when did it last speak"*
    are different questions, and only one of them is recoverable if we overwrite it."""
    r = roster_seam.record_seen(roster_seam.new_roster(), "d_1", at=T0)
    r = roster_seam.record_seen(r, "d_1", at=T0 + 500)
    r = roster_seam.record_seen(r, "d_1", at=T0 + 900)
    row = r["devices"]["d_1"]
    assert row["first_seen"] == T0
    assert row["last_seen"] == T0 + 900
    assert row["sightings"] == 3


def test_a_clock_that_stepped_backwards_cannot_move_first_seen_forward():
    """NTP stepping the appliance at boot is the realistic way to get an out-of-order
    sighting. `min()` rather than "the first one we happened to store"."""
    r = roster_seam.record_seen(roster_seam.new_roster(), "d_1", at=T0)
    r = roster_seam.record_seen(r, "d_1", at=T0 - 100)
    assert r["devices"]["d_1"]["first_seen"] == T0 - 100


def test_the_cap_evicts_the_least_recently_seen():
    """The roster drives publishes on every broker connect, so an unbounded roster is an
    unbounded reconnect burst — and it is the growth §5.3's A9 exists to catch. The
    eviction order is the only one that keeps the robots actually in use."""
    r = roster_seam.new_roster()
    for i in range(10):
        r = roster_seam.record_seen(r, f"d_{i}", at=T0 + i, cap=3)
    assert set(r["devices"]) == {"d_7", "d_8", "d_9"}
    # And re-seeing an old one rescues it from the next eviction.
    r = roster_seam.record_seen(r, "d_7", at=T0 + 100, cap=3)
    r = roster_seam.record_seen(r, "d_99", at=T0 + 101, cap=3)
    assert "d_7" in r["devices"] and "d_8" not in r["devices"]


def test_ids_come_back_most_recently_seen_first():
    """The order a reconnect burst should go out in: the robot that spoke most recently is
    the one most likely to still be listening."""
    r = roster_seam.new_roster()
    for i, d in enumerate(["d_old", "d_mid", "d_new"]):
        r = roster_seam.record_seen(r, d, at=T0 + i * 100)
    assert roster_seam.device_ids(r) == ["d_new", "d_mid", "d_old"]


def test_forget_removes_a_device_a_parent_unpaired():
    r = roster_seam.record_seen(roster_seam.new_roster(), "d_1", at=T0)
    r = roster_seam.record_seen(r, "d_2", at=T0)
    assert set(roster_seam.forget(r, "d_1")["devices"]) == {"d_2"}
    # Forgetting something absent is not an error — an unpair may race a first sighting.
    assert set(roster_seam.forget(r, "d_nope")["devices"]) == {"d_1", "d_2"}


def test_resume_targets_skips_robots_we_already_have_evidence_of():
    """A robot in `self.robots` is handled by the paths that always handled it —
    `_device_connect`'s settle timer pushes its config — so including it here would double
    every push on a reconnect for nothing."""
    r = roster_seam.new_roster()
    for d in ("d_1", "d_2", "d_3"):
        r = roster_seam.record_seen(r, d, at=T0)
    assert set(roster_seam.resume_targets(r, connected=["d_2"])) == {"d_1", "d_3"}


def test_resume_targets_will_not_push_at_a_robot_a_parent_unpaired():
    """The pairing gate lives on the transport boundary and refuses *events*; it does not
    refuse a push we initiate. Without this subtraction the roster would cheerfully serve
    config to a robot the family gave away, which is the one thing the permit list is
    for."""
    r = roster_seam.new_roster()
    for d in ("d_ok", "d_revoked"):
        r = roster_seam.record_seen(r, d, at=T0)
    got = roster_seam.resume_targets(r, permitted=lambda d: d == "d_ok")
    assert got == ["d_ok"]
    # `None` means "no gate configured" (open fleet / SIL) and lets everything through,
    # exactly as the rest of the runtime does.
    assert set(roster_seam.resume_targets(r, permitted=None)) == {"d_ok", "d_revoked"}


def test_a_roster_file_someone_hand_edited_does_not_take_the_appliance_down():
    for junk in (None, [], "nope", {"devices": "not a dict"}, {"devices": {"d": 7}}):
        assert roster_seam.device_ids(junk) == []
        assert roster_seam.summarize(junk)["known"] == 0
        assert "d_1" in roster_seam.record_seen(junk, "d_1", at=T0)["devices"]


def test_summarize_is_a_count_and_two_timestamps_not_a_fleet_of_ids():
    """`/status` is polled every few seconds and no card renders an id list; the ids are
    on their own route."""
    r = roster_seam.record_seen(roster_seam.new_roster(), "d_1", at=T0)
    r = roster_seam.record_seen(r, "d_2", at=T0 + 60)
    s = roster_seam.summarize(r)
    assert s == {"known": 2, "oldest_first_seen": T0, "newest_last_seen": T0 + 60}


def test_resume_can_be_turned_off_without_losing_the_roster(monkeypatch):
    monkeypatch.setenv("MOXIE_ROSTER_RESUME", "0")
    assert roster_seam.resume_enabled() is False
    monkeypatch.setenv("MOXIE_ROSTER_RESUME", "off")
    assert roster_seam.resume_enabled() is False
    monkeypatch.delenv("MOXIE_ROSTER_RESUME")
    assert roster_seam.resume_enabled() is True


# --------------------------------------------------------------------------- #
# The runtime wiring
# --------------------------------------------------------------------------- #

def test_every_ingress_path_lands_a_robot_in_the_roster(tmp_path):
    """The broker log line, `_on_state`'s fallback and P0's C6 all converge on
    `_device_connect`, which is why the roster is written there and only there."""
    rt, _ = _rt(tmp_path)
    rt.client.up()
    rt._device_connect("d_from_log")
    rt._on_state("d_from_state", b"{}")
    rt._on_event("d_from_event", "some-event", b"{}")
    known = set(rt.roster()["devices"])
    assert {"d_from_log", "d_from_state", "d_from_event"} <= known


def test_the_roster_survives_the_process_that_wrote_it(tmp_path):
    """The whole point: a restarted supervisor is a second `JsonStore` over the same
    directory, and it must come up already knowing who it serves."""
    rt, _ = _rt(tmp_path)
    rt._device_connect("d_1")
    rt._device_connect("d_2")

    reborn, _ = _rt(tmp_path, device_id="d_fresh")
    assert {"d_1", "d_2"} <= set(reborn.roster()["devices"])
    assert reborn.status_snapshot()["roster"]["known"] >= 2


def test_a_rostered_robot_is_not_reported_as_connected(tmp_path):
    """**The negative property, and the reason this feature is not a lie.** A restart
    knows who it serves; it does not know who is *there*. `/status` must keep saying so."""
    rt, _ = _rt(tmp_path)
    rt._device_connect("d_1")

    reborn, live_id = _rt(tmp_path, device_id="d_live")
    assert "d_1" not in reborn.robots
    snap = reborn.status_snapshot()
    assert [r["device_id"] for r in snap["robots"]] == [live_id]
    assert snap["roster"]["known"] >= 1, "the roster is still known, just not claimed"


def test_a_restart_re_pushes_config_without_waiting_for_an_event(tmp_path):
    """The feature. After a restart the robot is still connected, `$SYS/broker/log` has
    nothing to replay and the robot will not re-publish `/state` — so before this the
    appliance simply said nothing until the child did."""
    rt, _ = _rt(tmp_path)
    rt.client.up()
    rt._device_connect("d_asleep")

    reborn, live_id = _rt(tmp_path, device_id="d_live")
    reborn.client.up()
    reborn.client.published.clear()
    pushed = reborn.resume_roster()
    assert "d_asleep" in pushed
    topics = [t for (t, _) in reborn.client.published]
    assert CONFIG.format(d="d_asleep") in topics
    # The live robot is not re-pushed: its own path already does that.
    assert CONFIG.format(d=live_id) not in topics


def test_the_resume_does_not_push_at_an_unpermitted_robot(tmp_path):
    """The permit gate reaches the push we initiate, not only the events we refuse."""
    rt, _ = _rt(tmp_path, allow_unverified_bots=False)
    rt.client.up()
    rt._device_connect("d_ok")
    rt._device_connect("d_revoked")
    rt.set_permit("d_ok", True)
    rt.robots.clear()

    rt.client.published.clear()
    pushed = rt.resume_roster()
    assert pushed == ["d_ok"]
    assert CONFIG.format(d="d_revoked") not in [t for (t, _) in rt.client.published]


def test_unpairing_a_robot_can_take_it_off_the_roster(tmp_path):
    rt, _ = _rt(tmp_path)
    rt._device_connect("d_gone")
    assert "d_gone" in rt.roster()["devices"]
    assert rt._roster_forget("d_gone") is True
    assert "d_gone" not in rt.roster()["devices"]


def test_the_resume_is_silent_when_it_is_turned_off(monkeypatch, tmp_path):
    monkeypatch.setenv("MOXIE_ROSTER_RESUME", "0")
    rt, _ = _rt(tmp_path)
    rt.client.up()
    rt._device_connect("d_1")
    rt.robots.clear()
    rt.client.published.clear()
    assert rt.resume_roster() == []
    assert rt.client.published == []
    # …and the roster is still recorded, so turning it back on needs no rediscovery.
    assert "d_1" in rt.roster()["devices"]


class _HeldTimer:
    """A `threading.Timer` stand-in that never fires on its own.

    The first draft of the storm test collapsed `ROSTER_RESUME_DELAY_S` to zero and let
    real timers run — and it failed, because at zero delay each timer fired *before* the
    next CONNACK bumped the generation, so every one of them was legitimately the newest.
    That is a race in the test, not a bug in the subject (at the real 1.0 s settle all six
    are queued long before any fires), and "make the delay smaller" is the wrong repair:
    it trades one timing assumption for a tighter one. Holding the callbacks and firing
    them explicitly asserts the generation logic itself, with no timing assumption at all —
    playbook rule 11, assert recorded state rather than a live sample.
    """

    pending: list = []

    def __init__(self, delay, fn):
        self.delay, self.fn, self.daemon = delay, fn, False
        _HeldTimer.pending.append(self)

    def start(self):
        pass

    @classmethod
    def fire_all(cls):
        held, cls.pending = list(cls.pending), []
        for t in held:
            t.fn()
        return held


def _held_timers(monkeypatch):
    import moxie_runtime
    _HeldTimer.pending = []
    monkeypatch.setattr(moxie_runtime.threading, "Timer", _HeldTimer)
    return _HeldTimer


def test_a_reconnect_storm_runs_one_resume_not_one_per_connack(monkeypatch, tmp_path):
    """paho's ladder can fire several CONNACKs inside the settle window on a flapping
    link. Without the generation check each would queue its own full-roster burst of
    publishes at a broker that is already struggling."""
    rt, _ = _rt(tmp_path)
    rt._device_connect("d_1")
    rt.robots.clear()
    timers = _held_timers(monkeypatch)
    runs = []
    rt.resume_roster = lambda: runs.append(rt._connect_generation)

    for _ in range(6):                         # six CONNACKs inside one settle window
        rt._connect_generation += 1
        rt._schedule_roster_resume()
    final = rt._connect_generation
    fired = timers.fire_all()

    assert len(fired) == 6, "every connect should still queue its own timer"
    assert runs == [final], f"a reconnect storm ran {len(runs)} resumes: {runs}"


def test_the_resume_timer_never_holds_a_shutdown_open(monkeypatch, tmp_path):
    """A non-daemon timer keeps the interpreter alive for its full delay after a SIGTERM —
    which turns "stop the container" into "stop the container in a second", every time."""
    rt, _ = _rt(tmp_path)
    timers = _held_timers(monkeypatch)
    rt._schedule_roster_resume()
    assert timers.pending and timers.pending[0].daemon is True
    assert timers.pending[0].delay == rt.ROSTER_RESUME_DELAY_S


def test_a_resume_scheduled_before_a_shutdown_does_not_publish(monkeypatch, tmp_path):
    """A stop must not be followed by a burst of config publishes from a timer that was
    already in flight."""
    rt, _ = _rt(tmp_path)
    rt._device_connect("d_1")
    rt.robots.clear()
    timers = _held_timers(monkeypatch)
    runs = []
    rt.resume_roster = lambda: runs.append(1)

    rt._schedule_roster_resume()
    rt._stopping = True                        # the SIGTERM lands while the timer waits
    timers.fire_all()
    assert runs == []


def test_a_resume_that_raises_does_not_kill_the_timer_thread(monkeypatch, tmp_path):
    """The timer body is the last thing standing between a store error and an unhandled
    exception in a thread nobody is watching (§5.3 A10 counts those)."""
    rt, _ = _rt(tmp_path)
    timers = _held_timers(monkeypatch)

    def boom():
        raise RuntimeError("the store went away")

    rt.resume_roster = boom
    rt._connect_generation += 1
    rt._schedule_roster_resume()
    timers.fire_all()                          # must not raise


def test_a_broken_store_never_costs_a_robot_its_connection(tmp_path):
    """`_roster_seen` runs on `_device_connect`, which runs on the MQTT loop. A roster
    write that raised there would take the connection down for a bookkeeping record."""
    rt, _ = _rt(tmp_path)

    def boom(*a, **kw):
        raise OSError("read-only /data")

    rt.store.write_shared = boom
    assert rt._roster_seen("d_1") is False
    rt._device_connect("d_2")                  # and the real path still registers it
    assert "d_2" in rt.robots
