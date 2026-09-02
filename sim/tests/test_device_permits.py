"""
The device allowlist / pairing gate (openmoxie-feature-audit.md §3.1, ADOPT quick win).

Our broker accepts anonymous connections — the robot's RS256 JWT is never verified, the
same LAN model the original ran ([mqtt §3b](../../docs/architecture/mqtt-and-conversation.md)).
Before this gate, *anything* that announced itself on `/devices/{id}/state` was pushed
`pairing_status:"paired"` **and the child's `child_pii`**. On a home network that is a
real exposure, so the appliance now keeps a permit list and is **closed by default**.

What is worth a test, and all of it is here:

  * `build_unpaired_cloud_config()` — the minimal document itself: never a `child_pii`;
  * the push seam — unpermitted ⇒ minimal, permitted ⇒ the full config, unchanged;
  * the three ways the gate opens (constructor · `MOXIE_ALLOW_UNVERIFIED_BOTS` · the
    durable fleet flag) and their precedence;
  * **service refusal** on the wire: a pending robot's turn never reaches the brain, its
    schedule pull gets the empty envelope, and its telemetry/audio/reports are dropped;
  * permit → an immediate full push; revoke → the next push is minimal again;
  * durability across a supervisor restart, and the console-facing snapshot fields.

No broker and no network: the runtime's MQTT client is `helpers_runtime.FakeClient` and
every runtime here gets its own `tmp_path` store, so nothing touches `mqtt/data/`.
"""
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# `server/moxie_server/fleet.py` is deliberately dependency-free (no fastapi here), so the
# console's normalizer is unit-testable in the same hermetic run as the runtime.
sys.path.insert(0, os.path.join(REPO, "server"))

from moxie_sdk.cloud_config import (build_robot_cloud_config,        # noqa: E402
                                    build_unpaired_cloud_config,
                                    PAIRED_PAIRING_STATUS,
                                    UNPAIRED_PAIRING_STATUS)
from moxie_sdk.store import JsonStore                                # noqa: E402

DEVICE = "d_pending"
CONFIG_TOPIC = "/devices/{d}/config"
CHAT_TOPIC = "/devices/{d}/commands/remote_chat"
QUERY_TOPIC = "/devices/{d}/commands/query_result"


# --------------------------------------------------------------------------- #
# the document itself
# --------------------------------------------------------------------------- #

def test_the_unpaired_config_never_carries_the_child():
    """The whole point of the gate. `child_pii` is the child's nickname + birthday."""
    cfg = build_unpaired_cloud_config()
    assert "child_pii" not in cfg
    assert "child" not in cfg
    blob = json.dumps(cfg)
    assert "nickname" not in blob and "birthday" not in blob


def test_the_unpaired_config_is_not_paired_and_uploads_nothing():
    cfg = build_unpaired_cloud_config()
    assert cfg["pairing_status"] == UNPAIRED_PAIRING_STATUS != PAIRED_PAIRING_STATUS
    # LoggingPolicy NO_DATA: an unpermitted device is not invited to upload to us either.
    assert cfg["data_sharing"] == "NO_DATA"
    # The `settings` envelope stays (the robot's config handler expects it) but carries
    # nothing about the household — and no `stt` prop, so we never ask for its microphone.
    assert "props" in cfg["settings"] and "stt" not in cfg["settings"]["props"]


def test_the_unpaired_config_is_a_strict_subset_of_nothing_sensitive():
    """Every key of the un-paired document is either the status, the privacy gate, or
    the settings envelope — a new leaky key has to be added deliberately, here."""
    assert set(build_unpaired_cloud_config()) == {
        "pairing_status", "data_sharing", "settings"}


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #

def _runtime(tmp_path, *, allow=None, devices=(DEVICE,), app=None):
    """A real `MoxieRuntime` with a fake transport, its own store, and the given robots
    already connected. `allow=None` = the shipped policy (closed unless the store or the
    environment says otherwise)."""
    pytest.importorskip("paho.mqtt.client", reason="the runtime imports paho")
    sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
    import moxie_runtime
    from helpers_runtime import FakeClient
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.types import ChildProfile, RobotContext

    class _Echo(MoxieApp):
        name = "echo"

        def __init__(self):
            self.turns = []
            self.connected = []

        def on_connect(self, robot):
            self.connected.append(robot.device_id)

        def respond(self, turn):
            self.turns.append(turn.speech)
            from moxie_sdk.types import Reply
            return Reply(text="the brain answered")

    rt = moxie_runtime.MoxieRuntime(app=app or _Echo(),
                                    child=ChildProfile(nickname="Sam"),
                                    allow_unverified_bots=allow)
    rt.store = JsonStore(root=str(tmp_path))
    rt.client = FakeClient()
    for d in devices:
        rt.robots[d] = RobotContext(device_id=d, child=rt.child)
    return rt


def _pushed(rt, device_id=DEVICE):
    msgs = rt.client.on(CONFIG_TOPIC.format(d=device_id))
    assert msgs, f"no config pushed to {device_id}"
    return msgs[-1]


def _wire_event(rt, name, payload, device_id=DEVICE):
    """Deliver one `/devices/{id}/events/{name}` message the way the broker does — through
    `_on_message`, which is where the gate lives."""
    class _Msg:
        topic = f"/devices/{device_id}/events/{name}"
    msg = _Msg()
    msg.payload = json.dumps(payload).encode()
    rt._on_message(None, None, msg)
    rt._pool.shutdown(wait=True)


# --------------------------------------------------------------------------- #
# the push seam
# --------------------------------------------------------------------------- #

def test_an_unpermitted_robot_gets_the_minimal_config(tmp_path):
    rt = _runtime(tmp_path)
    assert rt.is_permitted(DEVICE) is False
    cfg = rt._push_config(DEVICE)
    assert cfg == build_unpaired_cloud_config()
    assert _pushed(rt) == cfg
    assert "child_pii" not in _pushed(rt)


def test_a_permitted_robot_gets_exactly_the_old_full_config(tmp_path):
    rt = _runtime(tmp_path)
    rt.set_permit(DEVICE, True, label="Sam's Moxie")
    cfg = _pushed(rt)                       # set_permit re-pushes on the spot
    assert cfg == build_robot_cloud_config(rt.child)
    assert cfg["pairing_status"] == "paired"
    assert cfg["child_pii"]["nickname"] == "Sam"


def test_the_fleet_flag_opens_the_gate_for_everyone(tmp_path):
    rt = _runtime(tmp_path, devices=("d_a", "d_b"))
    rt.set_allow_unverified_bots(True)      # re-pushes every connected robot
    for d in ("d_a", "d_b"):
        assert _pushed(rt, d)["pairing_status"] == "paired"
    rt.set_allow_unverified_bots(False)
    for d in ("d_a", "d_b"):
        assert _pushed(rt, d)["pairing_status"] == UNPAIRED_PAIRING_STATUS


def test_the_env_switch_keeps_a_pre_gate_deployment_working(tmp_path, monkeypatch):
    """`MOXIE_ALLOW_UNVERIFIED_BOTS=1` is the migration escape hatch: an appliance that
    was already running keeps serving its robot without anyone touching the console."""
    monkeypatch.setenv("MOXIE_ALLOW_UNVERIFIED_BOTS", "1")
    rt = _runtime(tmp_path)
    assert rt.allow_unverified_bots() is True
    assert rt._push_config(DEVICE)["child_pii"]["nickname"] == "Sam"


def test_the_env_switch_can_also_pin_the_gate_shut(tmp_path, monkeypatch):
    """An explicit `0` beats the stored flag, so an operator can lock an appliance down
    from the environment without hunting for the toggle."""
    rt = _runtime(tmp_path)
    rt.set_allow_unverified_bots(True)
    monkeypatch.setenv("MOXIE_ALLOW_UNVERIFIED_BOTS", "0")
    assert rt.allow_unverified_bots() is False
    assert rt._push_config(DEVICE)["pairing_status"] == UNPAIRED_PAIRING_STATUS


def test_precedence_constructor_beats_env_beats_store(tmp_path, monkeypatch):
    monkeypatch.setenv("MOXIE_ALLOW_UNVERIFIED_BOTS", "0")
    rt = _runtime(tmp_path, allow=True)
    assert rt.allow_unverified_bots() is True           # constructor wins
    rt2 = _runtime(tmp_path)
    rt2.set_allow_unverified_bots(True)
    assert rt2.allow_unverified_bots() is False         # env beats the stored flag


# --------------------------------------------------------------------------- #
# permit / revoke, live
# --------------------------------------------------------------------------- #

def test_permitting_a_pending_robot_pushes_its_full_config_immediately(tmp_path):
    """No reconnect, no restart: the parent clicks Permit and the robot is paired."""
    rt = _runtime(tmp_path)
    rt._push_config(DEVICE)
    assert _pushed(rt)["pairing_status"] == UNPAIRED_PAIRING_STATUS
    rt.set_permit(DEVICE, True)
    assert _pushed(rt)["pairing_status"] == "paired"
    assert _pushed(rt)["child_pii"]["nickname"] == "Sam"
    # …and the app finally learns about the robot it may now talk to.
    assert rt.app.connected == [DEVICE]


def test_revoking_makes_the_next_push_minimal_again(tmp_path):
    rt = _runtime(tmp_path)
    rt.set_permit(DEVICE, True)
    rt.set_permit(DEVICE, False)
    assert _pushed(rt) == build_unpaired_cloud_config()
    assert rt.is_permitted(DEVICE) is False


def test_a_permit_needs_a_device_id(tmp_path):
    rt = _runtime(tmp_path)
    with pytest.raises(ValueError):
        rt.set_permit("", True)


def test_permits_survive_a_supervisor_restart(tmp_path):
    """The permit list is durable (`fleet/permits.json`), beside `fleet/config.json` and
    never inside `robots/` — so it can never collide with a device id."""
    rt = _runtime(tmp_path)
    rt.set_permit(DEVICE, True, label="Sam's Moxie")
    assert os.path.isfile(os.path.join(str(tmp_path), "fleet", "permits.json"))

    fresh = _runtime(tmp_path)              # same data dir, brand-new runtime
    assert fresh.is_permitted(DEVICE) is True
    assert fresh.permits()["devices"][DEVICE]["label"] == "Sam's Moxie"
    assert fresh._push_config(DEVICE)["child_pii"]["nickname"] == "Sam"


def test_a_permit_written_by_another_process_is_picked_up(tmp_path):
    """The record is memoized on the hot path, so prove the memo still follows the file:
    a permit written behind the runtime's back (another process, a hand edit) must take
    effect on the next connect without a restart."""
    rt = _runtime(tmp_path)
    assert rt.is_permitted(DEVICE) is False
    other = JsonStore(root=str(tmp_path))       # stands in for a second process
    other.write_shared("permits", {"allow_unverified_bots": False,
                                   "devices": {DEVICE: {"permitted_at": 1, "label": "x"}}})
    assert rt.is_permitted(DEVICE) is True
    assert rt._push_config(DEVICE)["pairing_status"] == "paired"


def test_a_corrupt_permit_file_fails_closed(tmp_path):
    """A damaged record must never read as "everything is allowed"."""
    os.makedirs(os.path.join(str(tmp_path), "fleet"), exist_ok=True)
    with open(os.path.join(str(tmp_path), "fleet", "permits.json"), "w") as fh:
        fh.write("{not json")
    rt = _runtime(tmp_path)
    assert rt.is_permitted(DEVICE) is False
    assert rt.allow_unverified_bots() is False


# --------------------------------------------------------------------------- #
# service refusal on the wire
# --------------------------------------------------------------------------- #

def test_a_pending_robots_turn_never_reaches_the_brain(tmp_path):
    rt = _runtime(tmp_path)
    _wire_event(rt, "remote-chat", {"event_id": "e1", "command": "prompt",
                                    "backend": "router", "speech": "hello Moxie"})
    assert rt.app.turns == [], "the brain answered an unpermitted device"
    replies = rt.client.on(CHAT_TOPIC.format(d=DEVICE))
    assert len(replies) == 1
    text = (replies[0].get("output") or {}).get("text", "")
    assert "not connected to a family" in text
    assert "Sam" not in json.dumps(replies[0]), "the reply named the child"
    assert rt.history.get(DEVICE, []) == [], "a pending device got a conversation record"


def test_a_permitted_robots_turn_is_answered_normally(tmp_path):
    rt = _runtime(tmp_path, allow=True)
    _wire_event(rt, "remote-chat", {"event_id": "e1", "command": "prompt",
                                    "backend": "router", "speech": "hello Moxie"})
    assert rt.app.turns == ["hello Moxie"]
    replies = rt.client.on(CHAT_TOPIC.format(d=DEVICE))
    assert (replies[-1]["output"]["text"]) == "the brain answered"


def test_a_pending_robots_schedule_pull_gets_the_empty_envelope(tmp_path):
    """The robot's pull must *resolve* rather than hang — but with nothing in it."""
    rt = _runtime(tmp_path)
    _wire_event(rt, "client-service-activity-log",
                {"subtopic": "query", "query": "schedule", "request_id": "rq-1"})
    answers = rt.client.on(QUERY_TOPIC.format(d=DEVICE))
    assert len(answers) == 1
    assert answers[0]["request_id"] == "rq-1"
    assert answers[0]["schedule"] == {}
    assert rt.build_schedule_for(DEVICE) != {}, "a real schedule exists; it was withheld"


def test_a_pending_robots_reports_and_telemetry_are_dropped(tmp_path):
    """Nothing an unpermitted device says is written to our durable store."""
    rt = _runtime(tmp_path)
    _wire_event(rt, "client-service-activity-log",
                {"mentor_behavior": {"module_id": "DM", "action": "COMPLETED"}})
    _wire_event(rt, "telemetry", {"event_name": "conversation_start", "recorded_at": 1})
    _wire_event(rt, "zmq", {"anything": True})
    assert rt.mentor_behaviors(DEVICE) == []
    assert rt.robots[DEVICE].extra.get("telemetry") in (None, [])
    assert rt.client.published == []


def test_a_pending_robots_notify_is_not_kept(tmp_path):
    rt = _runtime(tmp_path)
    _wire_event(rt, "remote-chat", {"command": "notify", "backend": "router",
                                    "speech": "I said something",
                                    "extra_lines": [{"context_type": "input",
                                                     "text": "the child said something"}]})
    assert rt.history.get(DEVICE, []) == []
    assert rt.client.published == []


def test_a_pending_robots_module_query_still_answers_empty(tmp_path):
    """`backend:data / query:modules` is the robot asking what content exists; answering
    it with an empty list costs nothing and keeps the robot's own loop unstuck."""
    rt = _runtime(tmp_path)
    _wire_event(rt, "remote-chat", {"event_id": "e9", "command": "prompt",
                                    "backend": "data", "query": "modules"})
    replies = rt.client.on(CHAT_TOPIC.format(d=DEVICE))
    assert replies[-1]["modules"] == []


def test_state_is_still_ingested_so_a_pending_robot_is_visible(tmp_path):
    """`/state` is the ONE thing a pending device is still listened to for — otherwise
    nobody could ever see it in the console in order to permit it."""
    rt = _runtime(tmp_path, devices=())

    class _Msg:
        topic = f"/devices/{DEVICE}/state"
    msg = _Msg()
    msg.payload = json.dumps({"robot_firmware_version": "v24.10.803",
                              "battery_level": 0.9}).encode()
    rt._on_message(None, None, msg)
    assert DEVICE in rt.robots
    assert rt.robots[DEVICE].firmware == "v24.10.803"
    assert rt.pending_robots() == [DEVICE]


# --------------------------------------------------------------------------- #
# what the console reads
# --------------------------------------------------------------------------- #

def test_status_snapshot_carries_the_gate(tmp_path):
    rt = _runtime(tmp_path, devices=("d_a", "d_b"))
    rt.set_permit("d_a", True, label="Sam's Moxie")
    snap = rt.status_snapshot()
    json.dumps(snap)                                    # the console reads this as JSON
    assert snap["allow_unverified_bots"] is False
    assert snap["pending_count"] == 1
    a = next(r for r in snap["robots"] if r["device_id"] == "d_a")
    b = next(r for r in snap["robots"] if r["device_id"] == "d_b")
    assert a["permitted"] is True and a["pending"] is False
    assert a["permit_label"] == "Sam's Moxie"
    assert b["permitted"] is False and b["pending"] is True


def test_permits_view_is_what_the_console_lists(tmp_path):
    rt = _runtime(tmp_path, devices=("d_a", "d_b"))
    rt.set_permit("d_a", True, label="Sam's Moxie")
    view = rt.permits_view()
    assert view["ok"] is True
    assert view["allow_unverified_bots"] is False
    assert [p["device_id"] for p in view["permits"]] == ["d_a"]
    assert view["pending"] == ["d_b"]
    assert view["connected"] == ["d_a", "d_b"]


def test_the_enforced_flag_and_the_stored_flag_are_reported_separately(tmp_path,
                                                                      monkeypatch):
    """A console that showed only the stored flag would lie to a parent whose appliance
    is open because of the environment variable."""
    monkeypatch.setenv("MOXIE_ALLOW_UNVERIFIED_BOTS", "1")
    rt = _runtime(tmp_path)
    view = rt.permits_view()
    assert view["allow_unverified_bots"] is True        # what is ENFORCED
    assert view["allow_unverified_bots_stored"] is False


def test_the_fleet_normalizer_surfaces_pending_robots():
    """`server/moxie_server/fleet.py` is pure, so the console shape tests here."""
    from moxie_server.fleet import normalize_fleet
    out = normalize_fleet({
        "ok": True, "app": "echo", "uptime_s": 3,
        "allow_unverified_bots": False, "pending_count": 1,
        "robots": [
            {"device_id": "d_a", "permitted": True, "pending": False,
             "permit_label": "Sam's Moxie"},
            {"device_id": "d_b", "permitted": False, "pending": True},
        ]})
    assert out["allow_unverified_bots"] is False
    assert out["pending"] == ["d_b"] and out["pending_count"] == 1
    a, b = out["robots"]
    assert a["permitted"] is True and a["permit_label"] == "Sam's Moxie"
    assert b["pending"] is True
    assert "pending — not permitted" in b["summary"]


def test_a_pre_gate_snapshot_still_renders_as_permitted():
    """An older supervisor sends neither key; it served everything, so that is how its
    robots must read back — the console must not paint a working robot as pending."""
    from moxie_server.fleet import normalize_fleet
    out = normalize_fleet({"ok": True, "robots": [{"device_id": "d_old"}]})
    assert out["robots"][0]["permitted"] is True
    assert out["robots"][0]["pending"] is False
    assert out["pending_count"] == 0

