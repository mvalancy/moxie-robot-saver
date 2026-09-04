"""
The **activity record** on disk, through the parent's privacy switch — the last two gaps.

`test_transcript_memory_policy.py` closed the transcript half of the same promise. Two
halves were left, both admitted in `moxie_runtime.py`'s own comments:

  1. **Telemetry had a gate and no erasure path at all.** `do_DELETE` accepted only
     `/memory`, so a parent who moved the switch to `NO_DATA` stopped new writes and kept
     every packet and every day row already on disk, with nothing to press. §③ of
     `docs/architecture/config-and-telemetry-contract.md` says `NO_DATA` means *"nothing.
     No packet, no count, no day row. A restart finds an empty store."* — false the moment
     the switch was flipped rather than set.
  2. **`ingest_mentor_behavior` was ungated.** It writes a durable per-child behavioural
     log — which activity was finished, quit or refused, with a timestamp on each — and it
     was the last thing this appliance wrote about a child with no `LoggingPolicy` gate,
     so a `NO_DATA` robot still accumulated a behavioural profile while its telemetry and
     its transcript were being refused.

Every assertion here reads **the store back off disk** (`os.path.exists`, `json.load`,
`store.read`). A 200 is not evidence of an erase and a `False` return is not evidence of a
gate — a gate that returns False and writes the file anyway is exactly the bug. The
HTTP tests check the status code *and then* look at the files.

Non-vacuity is asserted throughout: every "nothing is written" test has a `NO_MEDIA`/`FULL`
twin that proves the same path DOES write, so the gate cannot pass by disabling the
feature.

Hermetic: fake MQTT transport, no brain, tmp store, no sleeps, no network.
"""
from __future__ import annotations

import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402

pytest.importorskip("paho.mqtt.client", reason="the runtime needs paho")

from helpers_runtime import make_runtime            # noqa: E402
from moxie_sdk import telemetry as T                # noqa: E402
from moxie_sdk.app import MoxieApp                  # noqa: E402
from moxie_sdk.cloud_config import LoggingPolicy    # noqa: E402
from moxie_sdk.store import JsonStore               # noqa: E402
from moxie_sdk.types import RobotContext            # noqa: E402

#: The behavioural log's file name, written out rather than imported from
#: `moxie_runtime.MENTOR_BEHAVIORS_COLLECTION`. This is the name a parent's data actually
#: has on disk (`mqtt/data/README.md`), and a suite that took it from the module under
#: test could be renamed into agreement with a bug. The two telemetry names come from
#: `moxie_sdk.telemetry`, which owns them and is not what is being gated here.
BEHAVIORS = "mentor_behaviors"


class _App(MoxieApp):
    name = "content"


# ---------------------------------------------------------------------------
# harness — everything roots at the test's own tmp_path
# ---------------------------------------------------------------------------

def _rt(tmp_path, **kw):
    return make_runtime(_App(), store=JsonStore(str(tmp_path)), **kw)


def _file(tmp_path, device_id, collection):
    return tmp_path / "robots" / device_id / f"{collection}.json"


def _on_disk(tmp_path, device_id) -> list:
    """Which of the three activity records exist **as files**, in a stable order.

    The whole suite is this function: not a counter the runtime keeps about itself, not a
    return value, not a status code — the directory listing a parent's data actually is."""
    order = (T.PACKETS_COLLECTION, T.DAILY_COLLECTION, BEHAVIORS)
    return [c for c in order if _file(tmp_path, device_id, c).exists()]


def _send(rt, device_id, name="wake", ts=1756800000, data=b""):
    """One real telemetry Packet through `ingest_telemetry` (the MQTT ingest path)."""
    return rt.ingest_telemetry(device_id, json.dumps(
        T.build_packet(name, data, moxie_id=device_id, recorded_at=ts)))


def _finish(rt, device_id, module_id="MODULE_MISSION", action="COMPLETED", ts=1756800000):
    """One real MentorBehavior report through `_on_activity` — the topic handler the
    robot's own report lands on, not `ingest_mentor_behavior` called directly. Gating the
    method and leaving the caller a way round it would be no gate at all."""
    return rt._on_activity(device_id, json.dumps(
        {"timestamp": ts, "mentor_behavior": {"module_id": module_id, "action": action,
                                              "content_id": "day1", "timestamp": ts}}))


def _drive(rt, device_id, tmp_path=None):
    """Everything a robot reports in a session: telemetry and a finished activity."""
    _send(rt, device_id)
    _finish(rt, device_id)


def _set_policy(rt, device_id, policy):
    """Move the switch **without** the config hooks, so a test can set up a robot whose
    history predates the flip (and so the erase tests are not secretly testing purge)."""
    rt._config_overrides.setdefault(device_id, {})["logging_policy"] = int(policy)


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _http(port, path, method="GET"):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=b"{}" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


# =========================================================================== #
# GAP 2 — the ungated behavioural log
# =========================================================================== #

def test_no_media_and_full_write_the_behaviour_log(tmp_path):
    """The non-vacuity twin, first, so the gate below cannot pass by writing nothing.

    `NO_MEDIA` is the default and it stores the record; so does `FULL`. A MentorBehavior
    has no opaque payload to withhold — it is `module_id`/`content_id`/`action` and a
    timestamp — so the choice is binary, like the transcript's."""
    rt, did = _rt(tmp_path)
    assert rt.telemetry_policy(did) == LoggingPolicy.NO_MEDIA     # nobody chose; default
    _finish(rt, did)
    assert BEHAVIORS in _on_disk(tmp_path, did)
    rows = json.loads(_file(tmp_path, did, BEHAVIORS).read_text())
    assert [r["module_id"] for r in rows] == ["MODULE_MISSION"]

    rt2, did2 = _rt(tmp_path, device_id="d_full")
    _set_policy(rt2, did2, LoggingPolicy.FULL)
    _finish(rt2, did2)
    assert json.loads(_file(tmp_path, did2, BEHAVIORS).read_text())


def test_no_data_writes_no_behaviour_log_to_disk(tmp_path):
    """THE DEFECT. A `NO_DATA` robot must accumulate no behavioural profile.

    Red before the gate landed: `ingest_mentor_behavior` appended through `self.store`
    with no policy check at all, so this file held the child's finished activities."""
    rt, did = _rt(tmp_path)
    _set_policy(rt, did, LoggingPolicy.NO_DATA)
    assert rt.telemetry_persists(did) is False
    for i in range(3):
        _finish(rt, did, module_id=f"MODULE_{i}")
    assert _on_disk(tmp_path, did) == []
    assert rt.mentor_behaviors(did) == []


def test_a_fleet_wide_no_data_rule_also_stops_the_behaviour_log(tmp_path):
    """The gate resolves through `telemetry_policy` → the **effective** (fleet ⊕ robot)
    config, so one house rule covers a robot with no override of its own."""
    rt, did = _rt(tmp_path)
    rt.store.write_shared(rt.FLEET_CONFIG_COLLECTION,
                          {"logging_policy": int(LoggingPolicy.NO_DATA)})
    _finish(rt, did)
    assert _on_disk(tmp_path, did) == []


def test_the_gate_is_persistence_only_the_turn_is_unaffected(tmp_path):
    """`ingest_mentor_behavior` still parses and returns the record under `NO_DATA`, and
    the console's live feed still gets its line.

    Same doctrine as the transcript gate and `ingest_telemetry`: this is a **persistence**
    gate. `self.recent` is a 120-entry deque that dies with the process and never reaches
    disk; blinding a parent's own console would be privacy theatre with a real cost."""
    rt, did = _rt(tmp_path)
    _set_policy(rt, did, LoggingPolicy.NO_DATA)
    rec = rt.ingest_mentor_behavior(did, {"mentor_behavior": {"module_id": "MODULE_X",
                                                             "action": "COMPLETED"}})
    assert rec == {"module_id": "MODULE_X", "action": "COMPLETED"}
    assert any(n["kind"] == "behavior" for n in rt.recent)
    assert _on_disk(tmp_path, did) == []


def test_a_policy_change_takes_effect_on_the_behaviour_log_without_a_restart(tmp_path):
    """Resolved per write, so a parent flipping the switch on a live robot does not have
    to restart the appliance — and can turn it back on."""
    rt, did = _rt(tmp_path)
    _finish(rt, did, module_id="BEFORE")
    assert BEHAVIORS in _on_disk(tmp_path, did)

    rt.update_config(did, logging_policy=int(LoggingPolicy.NO_DATA))
    _finish(rt, did, module_id="DURING")
    assert _on_disk(tmp_path, did) == []                    # the flip took the file too

    rt.update_config(did, logging_policy=int(LoggingPolicy.NO_MEDIA))
    _finish(rt, did, module_id="AFTER")
    rows = json.loads(_file(tmp_path, did, BEHAVIORS).read_text())
    assert [r["module_id"] for r in rows] == ["AFTER"]      # ...and only what came after


# =========================================================================== #
# GAP 1 — the erasure path that did not exist
# =========================================================================== #

def test_erase_telemetry_removes_all_three_records_from_disk(tmp_path):
    """The erase a parent presses. Proven by reading the store back, not by a return."""
    rt, did = _rt(tmp_path)
    _drive(rt, did)
    assert _on_disk(tmp_path, did) == [T.PACKETS_COLLECTION, T.DAILY_COLLECTION,
                                       BEHAVIORS]

    out = rt.erase_telemetry(did)
    assert out["ok"] is True and out["erased"] is True
    assert out["records"] == sorted([T.PACKETS_COLLECTION, T.DAILY_COLLECTION, BEHAVIORS])
    assert _on_disk(tmp_path, did) == []
    assert rt.store.read(did, T.PACKETS_COLLECTION, None) is None
    assert rt.mentor_behaviors(did) == []


def test_the_in_ram_view_agrees_with_the_disk_after_an_erase(tmp_path):
    """The half an erase is easy to get wrong: `_telemetry_buffer` caches the ring in
    `RobotContext.extra`, so deleting the files while that list survives would leave the
    console serving a stale hydrate of exactly what the parent just erased — and the next
    packet would re-persist it."""
    rt, did = _rt(tmp_path)
    for i in range(3):
        _send(rt, did, ts=1756800000 + i)
    assert len(rt._telemetry_buffer(did)) == 3
    assert rt.telemetry_view(did)["summary"]["count"] == 3

    rt.erase_telemetry(did)
    assert rt._telemetry_buffer(did) == []
    view = rt.telemetry_view(did)
    assert view["ok"] is True and view["summary"]["count"] == 0
    assert view["totals"]["total"] == 0 and view["events"] == []

    # and the next packet starts a NEW history rather than resurrecting the old one
    _send(rt, did, ts=1756900000)
    assert len(json.loads(_file(tmp_path, did, T.PACKETS_COLLECTION).read_text())) == 1


def test_erasing_is_idempotent_and_still_answers(tmp_path):
    """Erasing twice is not an error, and erasing the last of it is still a hit — the
    robot whose store we just emptied must not answer 404 to the parent who emptied it
    (the shape `erase_memory` already returns)."""
    rt, did = _rt(tmp_path)
    _drive(rt, did)
    assert rt.erase_telemetry(did)["erased"] is True
    again = rt.erase_telemetry(did)
    assert again["ok"] is True and again["erased"] is False and again["records"] == []
    assert _on_disk(tmp_path, did) == []


def test_erasing_a_robot_that_is_not_connected_still_works(tmp_path):
    """A parent asking to forget last week should not need the robot on the broker —
    the same rule `telemetry_view` already follows for reads."""
    rt, did = _rt(tmp_path)
    _drive(rt, did)
    rt.robots.pop(did)                                   # robot goes off the broker
    out = rt.erase_telemetry(did)
    assert out["ok"] is True and out["erased"] is True and out["connected"] is False
    assert _on_disk(tmp_path, did) == []


def test_the_erase_is_never_policy_gated(tmp_path):
    """"Reads and erase always work." A parent under FULL, under NO_MEDIA and under a
    switch they have just moved to NO_DATA can all delete."""
    for policy in (LoggingPolicy.FULL, LoggingPolicy.NO_MEDIA, LoggingPolicy.NO_DATA):
        did = f"d_{policy.name.lower()}"
        rt, _ = _rt(tmp_path, device_id=did)
        _set_policy(rt, did, LoggingPolicy.FULL)         # write it...
        _drive(rt, did)
        assert _on_disk(tmp_path, did)
        _set_policy(rt, did, policy)                     # ...then read the erase's gate
        assert rt.erase_telemetry(did)["erased"] is True
        assert _on_disk(tmp_path, did) == []


def test_erasing_one_robot_leaves_the_other_alone(tmp_path):
    """Per robot, not per box — one child's parent cannot erase another child's history."""
    rt, did = _rt(tmp_path)
    other = "d_sibling"
    rt.robots[other] = RobotContext(device_id=other, child=rt.child,
                                    module_id="FREE_CHAT", content_id="default")
    _drive(rt, did)
    _drive(rt, other)
    rt.erase_telemetry(did)
    assert _on_disk(tmp_path, did) == []
    assert _on_disk(tmp_path, other) == [T.PACKETS_COLLECTION, T.DAILY_COLLECTION,
                                         BEHAVIORS]


# --------------------------------------------------------------------------- #
# ...and the flip erases retroactively (the decision, asserted)
# --------------------------------------------------------------------------- #

def test_flipping_to_no_data_erases_the_record_already_on_disk(tmp_path):
    """The documented decision: unlike the *facts* store (which keeps what it stored so a
    parent can still read and correct it), the activity record goes with the switch.

    §③ promises `NO_DATA` means an empty store, and the insights card already tells a
    parent under `NO_DATA` that nothing is being saved — a surviving ring makes both of
    them lie. A parent who wants the history gone *without* changing the policy has the
    explicit erase instead."""
    rt, did = _rt(tmp_path)
    _drive(rt, did)
    assert len(_on_disk(tmp_path, did)) == 3

    rt.update_config(did, logging_policy=int(LoggingPolicy.NO_DATA))
    assert _on_disk(tmp_path, did) == []
    assert rt._telemetry_buffer(did) == []


def test_a_fleet_wide_no_data_rule_erases_every_robots_record(tmp_path):
    """A house rule reaches every robot on the box, including one that is not connected —
    which is the case the per-robot push loop cannot cover."""
    rt, did = _rt(tmp_path)
    absent = "d_absent"
    rt.robots[absent] = RobotContext(device_id=absent, child=rt.child,
                                     module_id="FREE_CHAT", content_id="default")
    _drive(rt, did)
    _drive(rt, absent)
    rt.robots.pop(absent)                                # stored, but off the broker
    assert len(_on_disk(tmp_path, absent)) == 3

    rt.update_fleet_config(logging_policy=int(LoggingPolicy.NO_DATA))
    assert _on_disk(tmp_path, did) == []
    assert _on_disk(tmp_path, absent) == []


def test_a_no_data_record_is_not_rehydrated_by_a_restart(tmp_path):
    """A durable fleet rule outlives the process; the files must not.

    Telemetry hydrates lazily rather than at boot, so without the boot sweep a restart
    under a fleet-wide `NO_DATA` would serve the old ring to the console the first time
    anyone looked — "a restart finds an empty store" is the contract's own sentence."""
    rt, did = _rt(tmp_path)
    _drive(rt, did)
    assert len(_on_disk(tmp_path, did)) == 3
    rt.store.write_shared(rt.FLEET_CONFIG_COLLECTION,       # written behind the hooks,
                          {"logging_policy": int(LoggingPolicy.NO_DATA)})   # as a crash
    assert len(_on_disk(tmp_path, did)) == 3                # mid-flip would leave it

    rt2, _ = _rt(tmp_path, device_id=did)                   # ...the next boot sweeps it
    assert _on_disk(tmp_path, did) == []
    assert rt2.telemetry_view(did)["summary"]["count"] == 0


def test_the_boot_sweep_leaves_an_ungated_robot_alone(tmp_path):
    """The other direction, so the sweep is not just "delete everything at boot"."""
    rt, did = _rt(tmp_path)
    _drive(rt, did)
    rt2, _ = _rt(tmp_path, device_id=did)
    assert _on_disk(tmp_path, did) == [T.PACKETS_COLLECTION, T.DAILY_COLLECTION,
                                       BEHAVIORS]
    assert rt2.telemetry_view(did)["summary"]["count"] == 1
    assert [r["module_id"] for r in rt2.mentor_behaviors(did)] == ["MODULE_MISSION"]


# --------------------------------------------------------------------------- #
# the route a parent's console actually calls
# --------------------------------------------------------------------------- #

def test_delete_telemetry_over_http_really_empties_the_store(tmp_path):
    """`DELETE /telemetry?device_id=…` — the endpoint that did not exist.

    The 200 is checked and then ignored: the assertion that matters is the directory."""
    rt, did = _rt(tmp_path)
    _drive(rt, did)
    port = _free_port()
    rt._start_status_server(port)

    code, body = _http(port, f"/telemetry?device_id={did}", method="DELETE")
    assert code == 200 and body["ok"] is True and body["erased"] is True
    assert _on_disk(tmp_path, did) == []
    # ...and the GET that shares the path still reads, so the route split is right
    code, body = _http(port, f"/telemetry?device_id={did}")
    assert code == 200 and body["summary"]["count"] == 0


def test_delete_telemetry_without_a_device_id_is_a_400(tmp_path):
    """No device_id is a client error, not an accidental fleet-wide wipe."""
    rt, did = _rt(tmp_path)
    _drive(rt, did)
    port = _free_port()
    rt._start_status_server(port)
    code, body = _http(port, "/telemetry", method="DELETE")
    assert code == 400 and body["ok"] is False
    assert len(_on_disk(tmp_path, did)) == 3        # and it erased nothing


def test_delete_still_serves_memory_and_still_404s_anything_else(tmp_path):
    """The route that was already there keeps working, and an unknown path is still a
    404 rather than falling through to an erase."""
    rt, did = _rt(tmp_path)
    rt.memory_store().merge(did, "mchat", {"facts": ["a fact"]})
    _drive(rt, did)
    port = _free_port()
    rt._start_status_server(port)

    code, body = _http(port, f"/memory?device_id={did}", method="DELETE")
    assert code == 200 and body["erased"] is True
    assert rt.memory_store().load(did) == {}
    assert len(_on_disk(tmp_path, did)) == 3        # memory's erase is not telemetry's

    code, _body = _http(port, f"/nonsense?device_id={did}", method="DELETE")
    assert code == 404
    assert len(_on_disk(tmp_path, did)) == 3
