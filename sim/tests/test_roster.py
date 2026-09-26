"""
The durable robot roster: a restart re-pushes config to every robot it has served.

`MoxieRuntime.robots` is memory-only and every presence signal is an event (the broker
log line is never replayed, `/state` is sent on the ROBOT's connect), so without a
roster a restarted supervisor stays silent until the child speaks.

The property guarded hardest is negative: a rostered robot must NOT be marked connected.
Inventing presence to populate `/status` would be a belief posing as an observation.

Hermetic: no broker, no network; the resume path is driven directly. Test names are
`-k` selectors in `sim/tools/hardening_p1_mutation_check.py` — keep them.
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

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


def _roster(*ids, at=T0, **kw):
    r = roster_seam.new_roster()
    for d in ids:
        r = roster_seam.record_seen(r, d, at=at, **kw)
    return r


def _topics(rt):
    return [t for (t, _) in rt.client.published]


def _outage(rt):
    """A broker blip: every robot is now unconfirmed."""
    rt.client.drop()
    rt.client.up()


# --------------------------------------------------------------------------- #
# The shapes
# --------------------------------------------------------------------------- #

def test_record_seen_never_mutates_the_roster_it_was_given():
    """The runtime read-modify-writes inside `transaction_shared()`, so a refused lock must
    leave the in-memory value untouched, not half-applied."""
    before = roster_seam.new_roster()
    after = roster_seam.record_seen(before, "d_1", at=T0)
    assert before == {"devices": {}}
    assert "d_1" in after["devices"]


def test_first_seen_survives_every_later_sighting():
    """"Served since when" and "last spoke when" are different questions."""
    r = _roster("d_1")
    r = roster_seam.record_seen(r, "d_1", at=T0 + 500)
    r = roster_seam.record_seen(r, "d_1", at=T0 + 900)
    row = r["devices"]["d_1"]
    assert row["first_seen"] == T0
    assert row["last_seen"] == T0 + 900
    assert row["sightings"] == 3


def test_a_clock_that_stepped_backwards_cannot_move_first_seen_forward():
    """NTP stepping the clock at boot yields out-of-order sightings: `min()`, not
    "whichever we stored first"."""
    r = roster_seam.record_seen(_roster("d_1"), "d_1", at=T0 - 100)
    assert r["devices"]["d_1"]["first_seen"] == T0 - 100


def test_the_cap_evicts_the_least_recently_seen():
    """An unbounded roster is an unbounded reconnect burst; LRU keeps the robots in use."""
    r = roster_seam.new_roster()
    for i in range(10):
        r = roster_seam.record_seen(r, f"d_{i}", at=T0 + i, cap=3)
    assert set(r["devices"]) == {"d_7", "d_8", "d_9"}
    # re-seeing an old one rescues it from the next eviction
    r = roster_seam.record_seen(r, "d_7", at=T0 + 100, cap=3)
    r = roster_seam.record_seen(r, "d_99", at=T0 + 101, cap=3)
    assert "d_7" in r["devices"] and "d_8" not in r["devices"]


def test_ids_come_back_most_recently_seen_first():
    """The burst order: the robot that spoke last is the likeliest still listening."""
    r = roster_seam.new_roster()
    for i, d in enumerate(["d_old", "d_mid", "d_new"]):
        r = roster_seam.record_seen(r, d, at=T0 + i * 100)
    assert roster_seam.device_ids(r) == ["d_new", "d_mid", "d_old"]


def test_forget_removes_a_device_a_parent_unpaired():
    r = _roster("d_1", "d_2")
    assert set(roster_seam.forget(r, "d_1")["devices"]) == {"d_2"}
    # forgetting something absent is not an error — an unpair may race a first sighting
    assert set(roster_seam.forget(r, "d_nope")["devices"]) == {"d_1", "d_2"}


def test_resume_targets_skips_robots_we_already_have_evidence_of():
    """A connected robot gets its push from `_device_connect`'s settle timer; including it
    here would double every push on a reconnect."""
    r = _roster("d_1", "d_2", "d_3")
    assert set(roster_seam.resume_targets(r, connected=["d_2"])) == {"d_1", "d_3"}


def test_resume_targets_will_not_push_at_a_robot_a_parent_unpaired():
    """The pairing gate refuses *events*, not pushes we initiate — without this the roster
    would serve config to a robot the family gave away."""
    r = _roster("d_ok", "d_revoked")
    assert roster_seam.resume_targets(r, permitted=lambda d: d == "d_ok") == ["d_ok"]
    # `None` = no gate configured (open fleet / SIL): everything through
    assert set(roster_seam.resume_targets(r, permitted=None)) == {"d_ok", "d_revoked"}


def test_a_roster_file_someone_hand_edited_does_not_take_the_appliance_down():
    for junk in (None, [], "nope", {"devices": "not a dict"}, {"devices": {"d": 7}}):
        assert roster_seam.device_ids(junk) == []
        assert roster_seam.summarize(junk)["known"] == 0
        assert "d_1" in roster_seam.record_seen(junk, "d_1", at=T0)["devices"]


def test_summarize_is_a_count_and_two_timestamps_not_a_fleet_of_ids():
    """`/status` is polled every few seconds; the ids are on their own route."""
    r = roster_seam.record_seen(_roster("d_1"), "d_2", at=T0 + 60)
    assert roster_seam.summarize(r) == {"known": 2, "oldest_first_seen": T0,
                                        "newest_last_seen": T0 + 60}


def test_resume_can_be_turned_off_without_losing_the_roster(monkeypatch):
    for off in ("0", "off"):
        monkeypatch.setenv("MOXIE_ROSTER_RESUME", off)
        assert roster_seam.resume_enabled() is False
    monkeypatch.delenv("MOXIE_ROSTER_RESUME")
    assert roster_seam.resume_enabled() is True


# --------------------------------------------------------------------------- #
# The runtime wiring
# --------------------------------------------------------------------------- #

def test_every_ingress_path_lands_a_robot_in_the_roster(tmp_path):
    """Broker log, `_on_state` and an event all converge on `_device_connect`, which is
    why the roster is written there and only there."""
    rt, _ = _rt(tmp_path)
    rt.client.up()
    rt._device_connect("d_from_log")
    rt._on_state("d_from_state", b"{}")
    rt._on_event("d_from_event", "some-event", b"{}")
    assert {"d_from_log", "d_from_state", "d_from_event"} <= set(rt.roster()["devices"])


def test_the_roster_survives_the_process_that_wrote_it(tmp_path):
    """A restarted supervisor is a second `JsonStore` over the same directory."""
    rt, _ = _rt(tmp_path)
    rt._device_connect("d_1")
    rt._device_connect("d_2")

    reborn, _ = _rt(tmp_path, device_id="d_fresh")
    assert {"d_1", "d_2"} <= set(reborn.roster()["devices"])
    assert reborn.status_snapshot()["roster"]["known"] >= 2


def test_a_rostered_robot_is_not_reported_as_connected(tmp_path):
    """A restart knows who it serves, not who is THERE. Asserted after `resume_roster()`,
    the only moment the lie can be told (mutation R10 must fail here)."""
    rt, _ = _rt(tmp_path)
    rt._device_connect("d_1")

    reborn, live_id = _rt(tmp_path, device_id="d_live")
    reborn.client.up()
    assert "d_1" not in reborn.robots

    pushed = reborn.resume_roster()
    assert "d_1" in pushed, "the resume did not reach the rostered robot at all"
    assert "d_1" not in reborn.robots, \
        "the resume invented presence for a robot it has no evidence of"
    snap = reborn.status_snapshot()
    assert [r["device_id"] for r in snap["robots"]] == [live_id]
    assert snap["roster"]["known"] >= 1, "the roster is still known, just not claimed"
    assert snap["pending_count"] == 0, "a rostered robot must not surface as pending either"


def test_a_restart_re_pushes_config_without_waiting_for_an_event(tmp_path):
    """The feature: after a restart the robot will not re-publish `/state`."""
    rt, _ = _rt(tmp_path)
    rt.client.up()
    rt._device_connect("d_asleep")

    reborn, live_id = _rt(tmp_path, device_id="d_live")
    reborn.client.up()
    reborn.client.published.clear()
    assert "d_asleep" in reborn.resume_roster()
    assert CONFIG.format(d="d_asleep") in _topics(reborn)
    # the live robot is not re-pushed: its own path already does that
    assert CONFIG.format(d=live_id) not in _topics(reborn)


def test_the_resume_does_not_push_at_an_unpermitted_robot(tmp_path):
    rt, _ = _rt(tmp_path, allow_unverified_bots=False)
    rt.client.up()
    rt._device_connect("d_ok")
    rt._device_connect("d_revoked")
    rt.set_permit("d_ok", True)
    rt.client.drop()

    rt.client.published.clear()
    assert rt.resume_roster() == ["d_ok"]
    assert CONFIG.format(d="d_revoked") not in _topics(rt)


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
    rt.client.drop()
    rt.client.published.clear()
    assert rt.resume_roster() == []
    assert rt.client.published == []
    # …and the roster is still recorded, so turning it back on needs no rediscovery
    assert "d_1" in rt.roster()["devices"]


class _HeldTimer:
    """A `threading.Timer` that never fires on its own, so the generation logic is tested
    with no timing assumption."""

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
    """Several CONNACKs inside one settle window must not each queue a full-roster burst
    at a broker that is already struggling."""
    rt, _ = _rt(tmp_path)
    rt._device_connect("d_1")
    rt.client.drop()
    timers = _held_timers(monkeypatch)
    runs = []
    rt.resume_roster = lambda: runs.append(rt._connect_generation)

    for _ in range(6):
        rt._connect_generation += 1
        rt._schedule_roster_resume()
    final = rt._connect_generation
    fired = timers.fire_all()

    assert len(fired) == 6, "every connect should still queue its own timer"
    assert runs == [final], f"a reconnect storm ran {len(runs)} resumes: {runs}"


def test_the_resume_timer_never_holds_a_shutdown_open(monkeypatch, tmp_path):
    """A non-daemon timer keeps the interpreter alive for its delay after a SIGTERM."""
    rt, _ = _rt(tmp_path)
    timers = _held_timers(monkeypatch)
    rt._schedule_roster_resume()
    assert timers.pending and timers.pending[0].daemon is True
    assert timers.pending[0].delay == rt.ROSTER_RESUME_DELAY_S


def test_a_resume_scheduled_before_a_shutdown_does_not_publish(monkeypatch, tmp_path):
    rt, _ = _rt(tmp_path)
    rt._device_connect("d_1")
    rt.client.drop()
    timers = _held_timers(monkeypatch)
    runs = []
    rt.resume_roster = lambda: runs.append(1)

    rt._schedule_roster_resume()
    rt._stopping = True                        # the SIGTERM lands while the timer waits
    timers.fire_all()
    assert runs == []


def test_a_resume_that_raises_does_not_kill_the_timer_thread(monkeypatch, tmp_path):
    """The timer body is all that stands between a store error and an unhandled exception
    in a thread nobody is watching."""
    rt, _ = _rt(tmp_path)
    timers = _held_timers(monkeypatch)

    def boom():
        raise RuntimeError("the store went away")

    rt.resume_roster = boom
    rt._connect_generation += 1
    rt._schedule_roster_resume()
    timers.fire_all()                          # must not raise


def test_two_supervisors_on_one_data_directory_do_not_lose_each_others_robots(tmp_path):
    """Two processes each register 30 robots through the runtime's REAL `_roster_seen`;
    neither may lose the other's (mutation R15 is invisible to single-writer tests).
    `__new__` because a full supervisor boot would make this about process startup."""
    script = (
        "import os, sys\n"
        f"sys.path.insert(0, {os.path.join(REPO, 'mqtt')!r})\n"
        f"sys.path.insert(0, {os.path.join(REPO, 'mqtt', 'supervisor')!r})\n"
        "from moxie_sdk.store import JsonStore\n"
        "from moxie_runtime import MoxieRuntime\n"
        "rt = MoxieRuntime.__new__(MoxieRuntime)\n"
        "rt.store = JsonStore(sys.argv[1])\n"
        "tag, n = sys.argv[2], int(sys.argv[3])\n"
        "for i in range(n):\n"
        "    assert rt._roster_seen(f'd_{tag}{i}'), 'the roster write was refused'\n")
    n = 30
    procs = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path), tag, str(n)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
             for tag in ("a", "b")]
    for p in procs:
        out, err = p.communicate(timeout=180)
        assert p.returncode == 0, err.decode()[-500:]

    store = JsonStore(str(tmp_path))
    known = set(store.read_shared(roster_seam.COLLECTION, {}).get("devices", {}))
    expected = {f"d_{tag}{i}" for tag in ("a", "b") for i in range(n)}
    assert known == expected, \
        f"{len(expected - known)} robot(s) were lost between two processes"


def test_the_runtimes_own_roster_write_is_inside_the_lock(tmp_path):
    """Structural pin: `_roster_seen` writes INSIDE `transaction_shared`."""
    rt, _ = _rt(tmp_path)
    order = []
    real_tx = rt.store.transaction_shared
    real_write = rt.store.write_shared

    @contextlib.contextmanager
    def traced_tx(collection):
        order.append(("enter", collection))
        with real_tx(collection):
            yield
        order.append(("exit", collection))

    rt.store.transaction_shared = traced_tx
    rt.store.write_shared = lambda c, v: (order.append(("write", c)), real_write(c, v))[1]
    assert rt._roster_seen("d_1") is True
    assert order == [("enter", roster_seam.COLLECTION),
                     ("write", roster_seam.COLLECTION),
                     ("exit", roster_seam.COLLECTION)], order


def test_a_broken_store_never_costs_a_robot_its_connection(tmp_path):
    """`_roster_seen` runs on the MQTT loop; a bookkeeping write must not take the
    connection down."""
    rt, _ = _rt(tmp_path)

    def boom(*a, **kw):
        raise OSError("read-only /data")

    rt.store.write_shared = boom
    assert rt._roster_seen("d_1") is False
    rt._device_connect("d_2")                  # and the real path still registers it
    assert "d_2" in rt.robots


# --------------------------------------------------------------------------- #
# The returning robot — a broker restart must not leave it half-connected
# --------------------------------------------------------------------------- #
# A broker restart loses the disconnect line, so a robot returning with the same id was
# never re-onboarded. The fix separates MEMBERSHIP (`self.robots`) from CONFIRMATION
# (`_seen_since_connect`, who we heard from on this socket); a disconnect clears only
# the latter. (Found by `sim/run_broker_outage.sh` 5c.)

class CountingApp(MoxieApp):
    """Counts `on_connect`, so "was the robot re-onboarded" is a number."""
    name = "test-onboard"

    def __init__(self):
        self.connects: list = []

    def on_connect(self, robot):
        self.connects.append(robot.device_id)

    def respond(self, turn):
        return Reply(text=f"You said: {turn.speech}")


def _counting_rt(tmp_path, **kw):
    app = CountingApp()
    rt, device_id = make_runtime(app, store=JsonStore(str(tmp_path)), **kw)
    rt.ROSTER_RESUME_DELAY_S = 0.0
    rt.client.up()
    return rt, device_id, app


def _settle(rt):
    """Let `_device_connect`'s real 1 s settle timer run."""
    done = threading.Event()
    threading.Timer(1.4, done.set).start()
    assert done.wait(10)


def _seen(rt):
    return {r["device_id"]: r["seen_since_connect"] for r in rt.status_snapshot()["robots"]}


def test_a_robot_returning_after_a_broker_restart_is_re_onboarded(tmp_path):
    """Same id, same `RobotContext`: it must get a config push and `app.on_connect`."""
    rt, device_id, app = _counting_rt(tmp_path)
    rt._device_connect("d_bear")
    _settle(rt)
    assert app.connects == ["d_bear"]

    _outage(rt)
    rt.client.published.clear()
    app.connects.clear()

    # the robot comes back and publishes `/state`, as a real Moxie does on ITS connect
    rt._on_state("d_bear", b'{"state":"config"}')
    _settle(rt)

    assert app.connects == ["d_bear"], "the returning robot never reached the app"
    assert CONFIG.format(d="d_bear") in _topics(rt), \
        "the returning robot got no config push"


def test_an_event_also_re_onboards_a_returning_robot(tmp_path):
    """A real Moxie may speak before it re-publishes `/state`; both paths must agree."""
    rt, _, app = _counting_rt(tmp_path)
    rt._device_connect("d_bear")
    _settle(rt)
    _outage(rt)
    app.connects.clear()

    rt._on_event("d_bear", "some-event", b"{}")
    _settle(rt)
    assert app.connects == ["d_bear"]


def test_the_returning_robot_keeps_its_history_and_its_context(tmp_path):
    """A child mid-conversation when the broker blinked continues it, not meets a
    stranger — which is why the robot is not dropped and re-created."""
    rt, _, app = _counting_rt(tmp_path)
    rt._device_connect("d_bear")
    before = rt.robots["d_bear"]
    before.extra["remember"] = "the dinosaur story"
    rt.history["d_bear"].append({"role": "user", "content": "tell me about dinosaurs"})

    _outage(rt)
    rt._on_state("d_bear", b"{}")

    assert rt.robots["d_bear"] is before, "the RobotContext was replaced, not reused"
    assert rt.robots["d_bear"].extra["remember"] == "the dinosaur story"
    assert len(rt.history["d_bear"]) == 1


def test_onboarding_is_idempotent_within_one_connection(tmp_path):
    """While the socket is up, repeated `/state` and events must NOT re-onboard."""
    rt, _, app = _counting_rt(tmp_path)
    rt._device_connect("d_bear")
    _settle(rt)
    assert app.connects == ["d_bear"]

    rt.client.published.clear()
    for _ in range(5):
        rt._on_state("d_bear", b"{}")
        rt._on_event("d_bear", "some-event", b"{}")
    _settle(rt)

    assert app.connects == ["d_bear"], f"re-onboarded {len(app.connects)} times on one socket"
    assert CONFIG.format(d="d_bear") not in _topics(rt)


def test_a_blip_does_not_re_onboard_a_robot_that_says_nothing(tmp_path):
    """A brief drop costs nothing until a robot gives evidence — the argument against
    clearing `self.robots` on disconnect. (The roster resume's one bounded config burst
    is allowed; `on_connect` for unheard robots is not.)"""
    rt, _, app = _counting_rt(tmp_path)
    for d in ("d_1", "d_2", "d_3"):
        rt._device_connect(d)
    _settle(rt)
    app.connects.clear()

    _outage(rt)
    rt.client.published.clear()
    _settle(rt)
    assert app.connects == [], "a socket blip re-onboarded robots that never spoke"


def test_status_labels_a_robot_we_have_not_heard_from_since_the_outage(tmp_path):
    """Ghosts are labelled, not deleted: our socket dying is evidence about us."""
    rt, harness_id, _ = _counting_rt(tmp_path)
    rt._device_connect("d_bear")
    rt._device_connect("d_fox")
    seen = _seen(rt)
    assert seen["d_bear"] is True and seen["d_fox"] is True
    # `make_runtime` hand-places its robot with no evidence, so it honestly reads as
    # unconfirmed — pinned so nobody "fixes" it
    assert seen[harness_id] is False

    _outage(rt)
    rt._on_state("d_bear", b"{}")           # only the bear comes back

    seen = _seen(rt)
    assert seen["d_bear"] is True, "the robot that came back is not marked present"
    assert seen["d_fox"] is False, "a robot that never came back is still claimed present"
    assert "d_fox" in rt.robots, "the ghost was deleted rather than labelled"


def test_the_broker_log_disconnect_still_removes_a_robot_properly(tmp_path):
    """A broker-reported disconnect IS evidence about the robot, so it leaves."""
    rt, _, app = _counting_rt(tmp_path)
    rt._device_connect("d_bear")
    rt._device_disconnect("d_bear")
    assert "d_bear" not in rt.robots
    assert "d_bear" not in rt._seen_since_connect
    assert [r["device_id"] for r in rt.status_snapshot()["robots"]] == ["d_test"]


def test_the_roster_resume_reaches_robots_the_outage_made_unconfirmed(tmp_path):
    """After a broker restart `self.robots` is every robot, so the resume subtracts the
    **confirmed** set — otherwise it would push to nobody in its own use case."""
    rt, _, _ = _counting_rt(tmp_path)
    rt._device_connect("d_bear")

    _outage(rt)
    rt.client.published.clear()
    assert "d_bear" in rt.resume_roster()
    assert CONFIG.format(d="d_bear") in _topics(rt)


# --------------------------------------------------------------------------- #
# Bench hermeticity: one SIL run's throwaway ids must not reach the next run's roster
# --------------------------------------------------------------------------- #

def test_every_sil_script_that_boots_a_supervisor_scopes_its_own_data_dir():
    sim_dir = os.path.join(REPO, "sim")
    offenders = []
    for name in sorted(os.listdir(sim_dir)):
        if not name.endswith(".sh"):
            continue
        src = open(os.path.join(sim_dir, name), encoding="utf-8").read()
        code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
        if "mqtt/run.py" in code and "MOXIE_DATA_DIR" not in code:
            offenders.append(name)
    assert not offenders, (
        f"{offenders} boot mqtt/run.py without scoping MOXIE_DATA_DIR, so they share the "
        f"repo's mqtt/data — and one run's throwaway d_<uuid> ids reach the next run's "
        f"roster resume. Add the per-run mktemp block sim/run_smoke.sh carries.")


def test_the_scoped_data_dir_is_removed_only_when_the_script_created_it():
    """The cleanup must never delete an operator's own `MOXIE_DATA_DIR`."""
    for name in ("run_smoke.sh", "run_scenarios.sh", "run_broker_outage.sh"):
        src = open(os.path.join(REPO, "sim", name), encoding="utf-8").read()
        assert 'MOXIE_DATA_DIR="$(mktemp -d' in src, name
        assert 'rm -rf "$MOXIE_DATA_DIR"' in src, name
        for line in src.splitlines():
            if 'rm -rf "$MOXIE_DATA_DIR"' in line:
                assert "MOXIE_DATA_DIR_OWNED" in line, \
                    f"{name}: the removal is not gated on having created the directory"
