"""
Fleet-level default config (openmoxie-feature-audit.md §4.1 ADOPT #6) — one appliance,
several robots, one place to set house rules.

The push is layered `defaults ⊕ fleet ⊕ per-robot`: the builder's own kwarg defaults,
then the appliance-wide record in the store (`fleet/config.json`), then this robot's own
overrides. Three things are worth a test and they are all here:

  * `merge_config_layers` — the pure precedence + deep-merge rule;
  * `JsonStore.read_shared`/`write_shared` — the fleet record, kept out of `robots/`;
  * the runtime seam — a fleet edit reaches **every** connected robot's `/config`, a
    per-robot override still wins, and the status snapshot stays JSON-safe.

No broker and no network: the runtime's MQTT client is `helpers_runtime.FakeClient`.
"""
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.cloud_config import merge_config_layers          # noqa: E402
from moxie_sdk.store import JsonStore                           # noqa: E402


# --------------------------------------------------------------------------- #
# merge_config_layers — the pure rule
# --------------------------------------------------------------------------- #

def test_later_layers_win_for_scalars():
    merged = merge_config_layers({"audio_volume": 0.2, "timezone_id": "UTC"},
                                 {"audio_volume": 0.9})
    assert merged == {"audio_volume": 0.9, "timezone_id": "UTC"}


def test_nested_objects_deep_merge_key_by_key():
    """A per-robot edit that touches one `settings.props` key keeps the fleet's rest."""
    fleet = {"settings": {"props": {"stt": "4", "doa_range": "80", "audio_wake": "1"}}}
    robot = {"settings": {"props": {"doa_range": "40"}}}
    merged = merge_config_layers(fleet, robot)
    assert merged["settings"]["props"] == {"stt": "4", "doa_range": "40", "audio_wake": "1"}


def test_lists_replace_rather_than_concatenate():
    """`weekday_bedtime` and `alarms.wakes` are lists: the robot's replaces the fleet's,
    it never appends to it (two bedtimes would be nonsense on the wire)."""
    merged = merge_config_layers({"weekday_bedtime": ["20:00", "07:00"]},
                                 {"weekday_bedtime": ["21:30", "06:30"]})
    assert merged["weekday_bedtime"] == ["21:30", "06:30"]
    merged = merge_config_layers(
        {"alarms": {"wakes": [{"days": [0], "time": "07:00"}], "enabled": True}},
        {"alarms": {"wakes": [{"days": [5, 6], "time": "09:00"}]}})
    assert merged["alarms"]["wakes"] == [{"days": [5, 6], "time": "09:00"}]
    assert merged["alarms"]["enabled"] is True          # untouched key survives


def test_an_explicit_none_from_the_robot_layer_clears_a_fleet_value():
    merged = merge_config_layers({"weekday_bedtime": ["20:00", "07:00"]},
                                 {"weekday_bedtime": None})
    assert merged["weekday_bedtime"] is None


def test_merge_never_mutates_its_inputs():
    fleet = {"settings": {"props": {"stt": "4"}}}
    robot = {"settings": {"props": {"stt": "0"}}}
    merged = merge_config_layers(fleet, robot)
    merged["settings"]["props"]["stt"] = "9"
    assert fleet["settings"]["props"]["stt"] == "4"
    assert robot["settings"]["props"]["stt"] == "0"


def test_empty_and_bad_layers():
    assert merge_config_layers(None, {}, {"a": 1}) == {"a": 1}
    with pytest.raises(ValueError):
        merge_config_layers({"a": 1}, [("a", 2)])


# --------------------------------------------------------------------------- #
# the store's fleet record
# --------------------------------------------------------------------------- #

def test_shared_records_live_beside_robots_never_inside_one(tmp_path):
    store = JsonStore(root=str(tmp_path))
    assert store.write_shared("config", {"audio_volume": 0.4}) is True
    assert store.read_shared("config") == {"audio_volume": 0.4}
    assert store.shared_path("config") == str(tmp_path / "fleet" / "config.json")
    # a robot named "config" cannot collide with it, and neither can read the other
    store.write("config", "config", {"audio_volume": 0.9})
    assert store.read_shared("config") == {"audio_volume": 0.4}
    assert store.devices() == ["config"]


def test_missing_shared_record_reads_the_default(tmp_path):
    store = JsonStore(root=str(tmp_path))
    assert store.read_shared("config", {}) == {}
    assert store.delete_shared("config") is False
    store.write_shared("config", {"a": 1})
    assert store.delete_shared("config") is True
    assert store.read_shared("config") is None


# --------------------------------------------------------------------------- #
# the console's pure view of the layers (server/moxie_server/fleet.py)
# --------------------------------------------------------------------------- #

def _console_fleet():
    """`moxie_server.fleet` is dependency-free on purpose, so it unit-tests here."""
    sys.path.insert(0, os.path.join(REPO, "server"))
    return pytest.importorskip("moxie_server.fleet", reason="console package not importable")


def test_config_sources_labels_the_layer_each_value_came_from():
    fleet = _console_fleet()
    sources = fleet.config_sources({"timezone_id": "UTC", "audio_volume": 0.2},
                                   {"audio_volume": 0.9, "alarms": None})
    assert sources == {"timezone_id": "fleet", "audio_volume": "robot", "alarms": "robot"}
    assert fleet.config_sources(None, None) == {}


def test_normalize_fleet_carries_the_fleet_layer_and_the_module_catalog():
    fleet = _console_fleet()
    from moxie_sdk.cloud_config import schedulable_module_ids
    view = fleet.normalize_fleet({
        "ok": True, "app": "content", "uptime_s": 1,
        "fleet_config": {"audio_volume": 0.25},
        "schedule_modules": list(schedulable_module_ids()),
        "robots": [{"device_id": "d_one", "config_overrides": {"screen_brightness": 0.5},
                    "config_effective": {"audio_volume": 0.25, "screen_brightness": 0.5}}],
    })
    assert view["fleet_config"] == {"audio_volume": 0.25}
    assert "JOKE" in view["schedule_modules"]
    robot = view["robots"][0]
    assert robot["config_effective"]["audio_volume"] == 0.25
    assert robot["config_sources"] == {"audio_volume": "fleet", "screen_brightness": "robot"}


def test_normalize_fleet_still_renders_a_pre_fleet_snapshot():
    """An older supervisor sends neither key — the console must not blow up or invent."""
    fleet = _console_fleet()
    view = fleet.normalize_fleet({"ok": True, "app": "content", "uptime_s": 1,
                                  "robots": [{"device_id": "d_one",
                                              "config_overrides": {"audio_volume": 0.4}}]})
    assert view["fleet_config"] == {} and view["schedule_modules"] == []
    assert view["robots"][0]["config_effective"] == {"audio_volume": 0.4}
    assert view["robots"][0]["config_sources"] == {"audio_volume": "robot"}


# --------------------------------------------------------------------------- #
# the runtime seam
# --------------------------------------------------------------------------- #

CONFIG_TOPIC = "/devices/{d}/config"


def _runtime(tmp_path, devices=("d_one", "d_two")):
    pytest.importorskip("paho.mqtt.client", reason="the runtime imports paho")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from helpers_runtime import make_runtime
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.types import RobotContext

    class _App(MoxieApp):
        name = "content"

    rt, first = make_runtime(_App(), device_id=devices[0])
    rt.store = JsonStore(root=str(tmp_path))
    for d in devices[1:]:
        rt.robots[d] = RobotContext(device_id=d, child=rt.child)
    return rt


def _pushed(rt, device_id):
    msgs = rt.client.on(CONFIG_TOPIC.format(d=device_id))
    assert msgs, f"no config pushed to {device_id}"
    return msgs[-1]


def test_a_fleet_edit_reaches_every_connected_robot(tmp_path):
    rt = _runtime(tmp_path)
    rt.update_fleet_config(audio_volume=0.25,
                           alarms={"wakes": [{"days": [0, 1, 2, 3, 4], "time": "07:00"}],
                                   "enabled": True})
    for device_id in ("d_one", "d_two"):
        cfg = _pushed(rt, device_id)
        assert cfg["audio_volume"] == 0.25
        assert cfg["alarms"] == {"wakes": [{"days": [0, 1, 2, 3, 4], "time": "07:00"}],
                                 "enabled": True}


def test_a_per_robot_override_wins_over_the_fleet_default(tmp_path):
    rt = _runtime(tmp_path)
    rt.update_fleet_config(audio_volume=0.25, timezone_id="America/Chicago")
    rt.update_config("d_one", audio_volume=0.8)
    one, two = _pushed(rt, "d_one"), _pushed(rt, "d_two")
    assert one["audio_volume"] == 0.8 and two["audio_volume"] == 0.25
    # the fleet key the robot did NOT override is still inherited
    assert one["timezone_id"] == "America/Chicago" == two["timezone_id"]


def test_no_fleet_config_means_exactly_the_old_behavior(tmp_path):
    """Nothing stored ⇒ the push is what `build_robot_cloud_config` alone would make."""
    from moxie_sdk.cloud_config import build_robot_cloud_config
    rt = _runtime(tmp_path, devices=("d_one",))
    rt._push_config("d_one")
    assert rt.fleet_config() == {}
    assert _pushed(rt, "d_one") == build_robot_cloud_config(rt.child)


def test_the_fleet_record_survives_a_restart(tmp_path):
    rt = _runtime(tmp_path, devices=("d_one",))
    rt.update_fleet_config(screen_brightness=0.3)
    fresh = _runtime(tmp_path, devices=("d_one",))          # same data dir, new runtime
    assert fresh.fleet_config() == {"screen_brightness": 0.3}
    fresh._push_config("d_one")
    assert _pushed(fresh, "d_one")["screen_brightness"] == 0.3


def test_status_snapshot_exposes_the_layers_and_stays_json_safe(tmp_path):
    rt = _runtime(tmp_path)
    rt.update_fleet_config(audio_volume=0.25)
    rt.update_config("d_one", alarms={"wakes": [{"days": [6], "time": "08:30"}],
                                      "enabled": True})
    snap = rt.status_snapshot()
    json.dumps(snap)                                    # the console reads this as JSON
    assert snap["fleet_config"] == {"audio_volume": 0.25}
    assert "JOKE" in snap["schedule_modules"]           # the on-board catalog, once
    one = next(r for r in snap["robots"] if r["device_id"] == "d_one")
    two = next(r for r in snap["robots"] if r["device_id"] == "d_two")
    assert "audio_volume" not in one["config_overrides"]         # per-robot layer only
    assert one["config_effective"]["audio_volume"] == 0.25       # inherited
    assert one["config_effective"]["alarms"]["wakes"][0]["days"] == [6]
    assert "alarms" not in two["config_effective"]               # not the other robot's
