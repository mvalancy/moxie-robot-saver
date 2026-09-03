"""
Durable telemetry through the REAL `MoxieRuntime` — write, restart, read it back.

`test_telemetry.py` owns the pure half (the envelope, the privacy filter, the caps and
the day arithmetic). This file owns the half that only the runtime can prove:

  * a Packet ingested by one supervisor is **still there for the next one** — the whole
    point of the slice, and the reason the 📈 card can now show a week;
  * the `LoggingPolicy` gate is really bound to the parent's per-robot config, for all
    three values, on the path that actually touches disk;
  * the existing in-memory read paths (`status_snapshot`'s `telemetry_count`, the
    schedule planner's packet buffer) see the history rather than only this process;
  * `wake_robot` publishes the recovered `wakeup` command on the recovered topic — and
    reports failure honestly when it cannot.

No broker, no robot: `helpers_runtime.make_runtime` gives a real runtime with a
recording transport, and every store is rooted at the test's own `tmp_path`.
"""
import json
import os
import sys
import time

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
sys.path.insert(0, os.path.dirname(__file__))

pytest.importorskip("paho.mqtt.client", reason="the runtime needs paho")

from helpers_runtime import make_runtime            # noqa: E402
from moxie_sdk import telemetry as T                # noqa: E402
from moxie_sdk.app import MoxieApp                  # noqa: E402
from moxie_sdk.cloud_config import LoggingPolicy    # noqa: E402
from moxie_sdk.store import JsonStore               # noqa: E402
from moxie_sdk.types import RobotContext            # noqa: E402


class _App(MoxieApp):
    name = "content"


def _rt(tmp_path, **kw):
    """A runtime whose durable store is this test's own directory."""
    return make_runtime(_App(), store=JsonStore(str(tmp_path)), **kw)


#: "Now", to the second. The daily roll-up is keyed on the LOCAL calendar day and
#: `history_view` counts back from today, so a test that wants to see its own row in the
#: week has to stamp its packets today — a fixed epoch from last year lands outside every
#: window, which is correct behaviour and a useless fixture.
TODAY = int(time.time())


def _send(rt, device_id, name, ts=None, data=b""):
    return rt.ingest_telemetry(device_id, json.dumps(
        T.build_packet(name, data, moxie_id=device_id, recorded_at=ts)))


# --------------------------------------------------------------------------- #
# The headline: history survives a restart
# --------------------------------------------------------------------------- #

def test_telemetry_survives_a_supervisor_restart(tmp_path):
    """Write through one runtime, throw it away, read it back through a NEW one against
    the same store. This is the bug the slice exists to fix: telemetry used to live in
    `RobotContext.extra["telemetry"]`, so a restart erased every answer."""
    rt, did = _rt(tmp_path)
    for i, name in enumerate(("wake", "conversation_start", "wake")):
        _send(rt, did, name, ts=TODAY - 30 + i)
    before = rt.telemetry_view(did)
    assert before["summary"]["count"] == 3

    # ---- the restart: a brand-new runtime object, a brand-new RobotContext ----
    rt2, _ = _rt(tmp_path, device_id=did)
    assert rt2.robots[did].extra.get("telemetry") is None, "nothing is loaded eagerly"

    after = rt2.telemetry_view(did)
    assert after["summary"]["count"] == 3, "the packet ring did not survive the restart"
    assert after["summary"]["by_event"] == {"wake": 2, "conversation_start": 1}
    assert after["totals"]["total"] == 3
    assert [e["event_name"] for e in after["events"]] == ["wake", "conversation_start",
                                                          "wake"]
    # and the daily roll-up came back too, so "last week" is answerable
    day = T.packet_day({"recorded_at": TODAY})
    today_row = [r for r in after["history"] if r["day"] == day]
    assert today_row and today_row[0]["count"] == 3
    assert len(after["history"]) == 7 and after["history"][-1]["day"] == day


def test_the_in_memory_read_paths_see_the_restored_history(tmp_path):
    """`telemetry_count` in the console snapshot and the schedule planner's packet
    buffer both read `extra["telemetry"]`. They must see history, not just this run."""
    rt, did = _rt(tmp_path)
    for i in range(4):
        _send(rt, did, "wake", ts=1756800000 + i)

    rt2, _ = _rt(tmp_path, device_id=did)
    robot = [r for r in rt2.status_snapshot()["robots"] if r["device_id"] == did][0]
    assert robot["telemetry_count"] == 4, "the console snapshot lost the history"
    # the planner's own view of the same buffer
    assert len(rt2._telemetry_buffer(did)) == 4
    _, _, inputs = rt2.plan_schedule_for(did)
    assert inputs["telemetry"]["count"] == 4


def test_history_is_readable_for_a_robot_that_is_not_connected(tmp_path):
    """A parent asking what happened last week should get an answer whether or not the
    robot is on the broker right now."""
    rt, did = _rt(tmp_path)
    _send(rt, did, "wake", ts=1756800000)

    rt2, _ = _rt(tmp_path, device_id="d_someone_else")
    view = rt2.telemetry_view(did)
    assert view["ok"] is True and view["connected"] is False
    assert view["summary"]["count"] == 1 and view["totals"]["total"] == 1


def test_a_device_with_no_history_at_all_is_still_a_404(tmp_path):
    rt, _ = _rt(tmp_path)
    missing = rt.telemetry_view("d_nope")
    assert missing["ok"] is False and "unknown device_id" in missing["error"]


def test_the_two_collections_are_where_the_contract_says(tmp_path):
    """The console and any later SQLite re-implementation both key off these names."""
    rt, did = _rt(tmp_path)
    _send(rt, did, "wake", ts=1756800000)
    assert (tmp_path / "robots" / did / f"{T.PACKETS_COLLECTION}.json").is_file()
    assert (tmp_path / "robots" / did / f"{T.DAILY_COLLECTION}.json").is_file()


# --------------------------------------------------------------------------- #
# The privacy gate, bound to the parent's own config — one test per value
# --------------------------------------------------------------------------- #

def _set_policy(rt, did, policy):
    rt._config_overrides.setdefault(did, {})["logging_policy"] = int(policy)


def test_no_data_writes_nothing_to_disk_at_all(tmp_path):
    """The child-privacy contract: under `NO_DATA` there is no packet file, no roll-up
    file, and the view says so instead of pretending the robot was quiet."""
    rt, did = _rt(tmp_path)
    _set_policy(rt, did, LoggingPolicy.NO_DATA)
    assert rt.telemetry_policy(did) == LoggingPolicy.NO_DATA
    assert rt.telemetry_persists(did) is False
    for i in range(3):
        _send(rt, did, "wake", ts=1756800000 + i, data=b"\x01\x02")

    robot_dir = tmp_path / "robots" / did
    assert not (robot_dir / f"{T.PACKETS_COLLECTION}.json").exists()
    assert not (robot_dir / f"{T.DAILY_COLLECTION}.json").exists()
    view = rt.telemetry_view(did)
    assert view["persisted"] is False and view["policy"] == "NO_DATA"
    assert view["totals"]["total"] == 0
    # and a restart really does leave nothing behind
    rt2, _ = _rt(tmp_path, device_id=did)
    assert rt2.telemetry_view(did)["summary"]["count"] == 0


def test_no_media_persists_the_envelope_and_never_the_payload(tmp_path):
    """The default. `Packet.event_data` is opaque `bytes`, so no payload is written —
    what a parent's insights need (what happened, when, in which session) is."""
    rt, did = _rt(tmp_path)
    _set_policy(rt, did, LoggingPolicy.NO_MEDIA)
    _send(rt, did, "wake", ts=1756800000, data=b"\x01\x02\x03")

    rows = json.loads((tmp_path / "robots" / did /
                       f"{T.PACKETS_COLLECTION}.json").read_text())
    assert len(rows) == 1
    assert "event_data" not in rows[0], "an opaque payload was written under NO_MEDIA"
    assert rows[0]["event_data_withheld"] == "NO_MEDIA"
    assert rows[0]["event_name"] == "wake" and rows[0]["recorded_at"] == 1756800000
    assert rt.telemetry_view(did)["policy"] == "NO_MEDIA"


def test_full_persists_the_payload_too(tmp_path):
    rt, did = _rt(tmp_path)
    _set_policy(rt, did, LoggingPolicy.FULL)
    _send(rt, did, "said", ts=1756800000, data=b"hello")

    rows = json.loads((tmp_path / "robots" / did /
                       f"{T.PACKETS_COLLECTION}.json").read_text())
    import base64
    assert base64.b64decode(rows[0]["event_data"]) == b"hello"
    assert rt.telemetry_view(did)["persisted"] is True


def test_the_default_policy_is_no_media_when_no_parent_has_chosen(tmp_path):
    """`RobotCloudConfig`'s own `data_sharing` default is NO_DATA — which is about what
    the ROBOT uploads. Defaulting the store to it would mean the feature never stored
    anything, so telemetry defaults to NO_MEDIA like the safety journal and memory."""
    rt, did = _rt(tmp_path)
    assert rt.telemetry_policy(did) == LoggingPolicy.NO_MEDIA
    assert rt.telemetry_persists(did) is True


def test_a_fleet_wide_privacy_switch_governs_every_robot(tmp_path):
    """A house rule set once for the appliance must reach a robot with no override of
    its own (the same layering `memory_policy` uses)."""
    rt, did = _rt(tmp_path)
    rt.store.write_shared(rt.FLEET_CONFIG_COLLECTION, {"logging_policy": 0})
    assert rt.telemetry_policy(did) == LoggingPolicy.NO_DATA
    _send(rt, did, "wake", ts=1756800000)
    assert not (tmp_path / "robots" / did / f"{T.PACKETS_COLLECTION}.json").exists()


def test_a_nonsense_policy_value_falls_back_to_the_default(tmp_path):
    rt, did = _rt(tmp_path)
    rt._config_overrides.setdefault(did, {})["logging_policy"] = "share-it-all"
    assert rt.telemetry_policy(did) == LoggingPolicy.NO_MEDIA


# --------------------------------------------------------------------------- #
# The caps hold on the real store
# --------------------------------------------------------------------------- #

def test_the_packet_ring_is_bounded_on_disk_and_in_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("MOXIE_TELEMETRY_MAX_PACKETS", "5")
    rt, did = _rt(tmp_path)
    for i in range(12):
        _send(rt, did, f"e{i}", ts=1756800000 + i)
    rows = json.loads((tmp_path / "robots" / did /
                       f"{T.PACKETS_COLLECTION}.json").read_text())
    assert len(rows) == 5 and rows[-1]["event_name"] == "e11"
    assert len(rt._telemetry_buffer(did)) == 5
    view = rt.telemetry_view(did)
    assert view["retention"]["packets"] == 5
    # the roll-up still knows the true total the ring can no longer show
    assert view["summary"]["count"] == 5 and view["totals"]["total"] == 12


def test_the_daily_window_is_bounded_on_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("MOXIE_TELEMETRY_MAX_DAYS", "3")
    rt, did = _rt(tmp_path)
    for i in range(8):
        _send(rt, did, "wake", ts=1756800000 + i * 86400)
    rollup = json.loads((tmp_path / "robots" / did /
                         f"{T.DAILY_COLLECTION}.json").read_text())
    assert len(rollup["days"]) == 3 and rollup["dropped_days"] == 5
    view = rt.telemetry_view(did, days=3)
    assert view["retention"]["days"] == 3 and len(view["history"]) == 3


def test_a_write_failure_never_costs_a_turn(tmp_path):
    """A telemetry write runs on the MQTT thread; a broken store must be logged and
    swallowed, not raised into the conversation loop."""
    rt, did = _rt(tmp_path)

    class _Broken:
        def read(self, *a, **k):
            return {}

        def read_shared(self, *a, **k):
            return {}

        def append(self, *a, **k):
            raise OSError("disk full")

        def write(self, *a, **k):
            raise OSError("disk full")

    rt.store = _Broken()
    pkt = _send(rt, did, "wake", ts=1756800000)
    assert pkt["event_name"] == "wake"              # the packet still came back
    assert len(rt.robots[did].extra["telemetry"]) == 1


# --------------------------------------------------------------------------- #
# `wakeup` — the console button that used to publish nothing
# --------------------------------------------------------------------------- #

def test_wake_robot_publishes_the_recovered_command(tmp_path):
    """`{"command":"wakeup"}` on `/devices/{id}/commands/wakeup` — the exact topic and
    payload `mqtt-and-conversation.md` §3.5 records. Nothing here is invented, and the
    reply never claims the robot woke: the corpus has no acknowledgement for it."""
    rt, did = _rt(tmp_path)
    out = rt.wake_robot(did)
    assert out["ok"] is True and out["published"] is True
    assert out["acknowledged"] is False
    assert out["topic"] == f"/devices/{did}/commands/wakeup"
    assert out["payload"] == {"command": "wakeup"}
    assert rt.client.on(f"/devices/{did}/commands/wakeup") == [{"command": "wakeup"}]


def test_wake_robot_warns_when_the_wake_button_is_switched_off(tmp_path):
    """The recovered command wakes a `wake_button_enabled` robot; a parent who turned
    that off should be told rather than left wondering why nothing happened."""
    rt, did = _rt(tmp_path)
    rt._config_overrides.setdefault(did, {})["wake_button_enabled"] = False
    out = rt.wake_robot(did)
    assert out["published"] is True and out["wake_button_enabled"] is False
    assert "wake button is switched off" in out["note"]


def test_wake_robot_is_honest_about_an_unknown_device(tmp_path):
    rt, _ = _rt(tmp_path)
    out = rt.wake_robot("d_nope")
    assert out["ok"] is False and out["published"] is False
    assert "unknown device_id" in out["error"] and out["reason"]
    assert not rt.client.published


def test_wake_robot_refuses_a_pending_robot(tmp_path):
    """A robot that reached the broker but is not permitted gets nothing from us — the
    same gate every other command respects."""
    rt, did = _rt(tmp_path, allow_unverified_bots=False)
    assert rt.is_permitted(did) is False
    out = rt.wake_robot(did)
    assert out["ok"] is False and out["published"] is False
    assert out["error"] == "robot is pending"
    assert not rt.client.published


def test_wake_robot_reports_a_missing_broker_rather_than_pretending(tmp_path):
    rt, did = _rt(tmp_path)
    rt.client = None
    out = rt.wake_robot(did)
    assert out["ok"] is False and out["published"] is False
    assert out["error"] == "no broker connection"


# --------------------------------------------------------------------------- #
# The status server: the routes the console proxies
# --------------------------------------------------------------------------- #

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


def test_status_server_serves_the_durable_view_and_the_wakeup_route(tmp_path):
    rt, did = _rt(tmp_path)
    for i in range(3):
        _send(rt, did, "wake", ts=1756800000 + i * 86400)
    port = _free_port()
    rt._start_status_server(port)

    code, body = _http(port, f"/telemetry?device_id={did}&limit=2&days=3")
    assert code == 200 and body["ok"] is True
    assert len(body["events"]) == 2 and len(body["history"]) == 3
    assert body["totals"]["total"] == 3 and body["retention"]["days"] >= 1
    assert body["policy"] == "NO_MEDIA" and body["persisted"] is True

    code, body = _http(port, "/telemetry?device_id=d_nope")
    assert code == 404 and body["ok"] is False

    code, body = _http(port, f"/wakeup?device_id={did}", method="POST")
    assert code == 200 and body["published"] is True
    assert rt.client.on(f"/devices/{did}/commands/wakeup") == [{"command": "wakeup"}]

    code, body = _http(port, "/wakeup?device_id=d_nope", method="POST")
    assert code == 404 and body["ok"] is False and body["published"] is False


def test_status_server_answers_409_for_a_pending_robot_wakeup(tmp_path):
    rt, did = _rt(tmp_path, allow_unverified_bots=False)
    port = _free_port()
    rt._start_status_server(port)
    code, body = _http(port, f"/wakeup?device_id={did}", method="POST")
    assert code == 409 and body["error"] == "robot is pending"
