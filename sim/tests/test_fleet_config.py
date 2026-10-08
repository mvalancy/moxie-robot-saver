"""
Fleet-level default config: one appliance, several robots, one place for house rules.

The push is layered `defaults ⊕ fleet ⊕ per-robot`. Tested here: the pure precedence +
deep-merge rule (`merge_config_layers`), the store's `fleet/config.json` record (kept out
of `robots/`), the console's view of the layers, and the runtime seam — a fleet edit
reaches **every** connected robot, a per-robot override still wins, and the status
snapshot stays JSON-safe. No broker: the transport is `helpers_runtime.FakeClient`.
"""
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

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
    """The robot's list replaces the fleet's (two bedtimes would be nonsense on the wire)."""
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
# the console's pure view of the layers (server/moxie_server/fleet/)
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


def _runtime(tmp_path, devices=("d_one", "d_two"), *, app=None, store=None):
    """A runtime on `tmp_path` with `devices` connected. The store goes to the constructor:
    that is where the runtime reads each robot's saved settings."""
    pytest.importorskip("paho.mqtt.client", reason="the runtime imports paho")
    from helpers_runtime import make_runtime
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.types import RobotContext

    class _App(MoxieApp):
        name = "content"

    rt, first = make_runtime(app or _App(), device_id=devices[0],
                             store=store or JsonStore(root=str(tmp_path)))
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


# --------------------------------------------------------------------------- #
# the per-robot layer outlives the process (robots/<id>/config.json)
# --------------------------------------------------------------------------- #
# The per-robot layer used to live in RAM only. A restart dropped it, and the roster
# resume then re-pushed the fleet-only document: volume, bedtime and look snapped back
# on the robot, the brain pick reset, and a per-robot NO_DATA reverted, so the child's
# words were kept again. Each test builds a second runtime on the same data dir: that is
# the restart.

def _record(tmp_path, device_id="d_one"):
    return tmp_path / "robots" / device_id / "config.json"


def _echo():
    """A brain that answers, so a turn reaches the transcript path."""
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.types import Reply

    class _Echo(MoxieApp):
        name = "echo"

        def respond(self, turn):
            return Reply(text="ok")

    return _Echo()


def test_a_per_robot_override_survives_a_restart(tmp_path, monkeypatch):
    """The settings are back before anything asks for them: brain, safety, lifecycle and
    /status read the per-robot dict directly, so a lazy read would leave them blind."""
    from helpers_runtime import http_json, status_server
    monkeypatch.delenv("MOXIE_APP", raising=False)          # no pin, so the pick stands
    rt = _runtime(tmp_path, devices=("d_one",))
    rt.update_config("d_one", weekday_bedtime=["19:30", "07:00"], audio_volume=0.3)
    rt.update_config("d_one", brain="llm")

    fresh = _runtime(tmp_path, devices=("d_one",))          # same data dir, new runtime
    assert fresh._config_overrides["d_one"] == {
        "weekday_bedtime": ["19:30", "07:00"], "audio_volume": 0.3, "brain": "llm"}
    cfg = fresh._push_config("d_one")
    assert cfg["audio_volume"] == 0.3
    assert (cfg["weekday_bedtime_enabled"], cfg["weekday_bedtime_starts_at"],
            cfg["weekday_bedtime_ends_at"]) == (True, "19:30", "07:00")
    assert "brain" not in cfg                               # still never sent to the robot
    assert fresh.brain_for("d_one")["source"] == "robot"
    snap = http_json(status_server(fresh) + "/status")
    one = next(r for r in snap["robots"] if r["device_id"] == "d_one")
    assert one["config_overrides"]["audio_volume"] == 0.3
    assert one["config_effective"]["weekday_bedtime"] == ["19:30", "07:00"]
    assert one["brain_source"] == "robot"


def test_a_per_robot_no_data_still_holds_after_a_restart(tmp_path, monkeypatch):
    """The privacy half: a parent who set one child's data sharing to NO_DATA must not
    have a restart quietly start keeping that child's words again."""
    from helpers_runtime import drive_turn
    from moxie_sdk.cloud_config import LoggingPolicy
    memdir = tmp_path / "transcripts"
    monkeypatch.setenv("MOXIE_MEMORY_DIR", str(memdir))     # read at construction
    rt = _runtime(tmp_path, devices=("d_one",), app=_echo())
    rt.update_config("d_one", logging_policy=int(LoggingPolicy.NO_DATA))

    fresh = _runtime(tmp_path, devices=("d_one",), app=_echo())
    assert fresh.safety_policy("d_one") == LoggingPolicy.NO_DATA   # reads the dict itself
    drive_turn(fresh, "d_one", "a-marker")
    assert not (memdir / "d_one.json").exists(), "the restart wrote the transcript again"
    assert fresh.memory_store().save("d_one", {"chat": {"facts": ["a-marker"]}}) is False
    assert not (tmp_path / "robots" / "d_one" / "memory.json").exists()

    # A transcript on disk at boot (a restored backup, a crash mid-flip) is erased by the
    # boot sweep, which runs after the settings are read; it is never loaded back.
    memdir.mkdir(exist_ok=True)
    (memdir / "d_one.json").write_text(json.dumps([{"role": "user", "content": "a-marker"}]))
    again = _runtime(tmp_path, devices=("d_one",), app=_echo())
    assert not (memdir / "d_one.json").exists()
    assert again.history.get("d_one") in (None, [])


def _fail_closed_lines(rt, device_id="d_one"):
    """The activity-feed lines saying this robot runs under NO_DATA because its saved
    data-sharing choice could not be read."""
    return [n for n in rt.recent if device_id in n["text"] and "NO_DATA" in n["text"]
            and "could not be read" in n["text"]]


@pytest.mark.parametrize("damage", ["{not json", "[1, 2]", "null", '"loud"'])
def test_a_damaged_record_fails_closed_and_never_breaks_construction(
        tmp_path, capsys, damage):
    """One bad file costs that robot its saved settings, never the appliance its boot.
    The data-sharing choice in it is unreadable too, so the robot FAILS CLOSED: it runs
    under NO_DATA, the most restrictive policy, until a parent saves its settings again
    (config-and-telemetry-contract.md: "a policy it cannot read fails closed rather than
    open"). Everything else is the no-override document. The log names the file once,
    and the activity feed says what it means in one line."""
    from moxie_sdk.cloud_config import LoggingPolicy, build_robot_cloud_config
    path = _record(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(damage)
    rt = _runtime(tmp_path, devices=("d_one",))
    assert rt._config_overrides["d_one"] == {"logging_policy": int(LoggingPolicy.NO_DATA)}
    assert (rt.safety_policy("d_one"), rt.memory_policy("d_one"),
            rt.telemetry_policy("d_one")) == (LoggingPolicy.NO_DATA,) * 3
    # The robot is told NO_DATA as well, which is also the document's own default.
    assert rt._push_config("d_one") == build_robot_cloud_config(rt.child)
    said = [ln for ln in capsys.readouterr().out.splitlines() if str(path) in ln]
    assert len(said) == 1, said
    feed = _fail_closed_lines(rt)
    assert len(feed) == 1 and feed[0]["kind"] == "error", list(rt.recent)
    assert feed[0] in rt.status_snapshot()["recent"]    # what the console's feed shows
    assert path.read_text() == damage                    # loading writes nothing


@pytest.mark.parametrize("stored", [7, "SOME_DATA", None, [0]])
def test_a_data_sharing_choice_the_whitelist_refuses_fails_closed(
        tmp_path, monkeypatch, stored):
    """A stored `logging_policy` the whitelist now refuses (a hand edit, a value from
    another build) is a choice a parent made that cannot be read. Dropping it would hand
    the child to the house default and start keeping their words again, so the robot
    runs under NO_DATA instead, in every sense: as for any NO_DATA robot the boot sweep
    clears a transcript already on disk. Its other settings still load."""
    from helpers_runtime import drive_turn
    from moxie_sdk.cloud_config import LoggingPolicy
    memdir = tmp_path / "transcripts"
    monkeypatch.setenv("MOXIE_MEMORY_DIR", str(memdir))     # read at construction
    memdir.mkdir()
    (memdir / "d_one.json").write_text(json.dumps([{"role": "user", "content": "old"}]))
    path = _record(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"audio_volume": 0.3, "logging_policy": stored}))
    rt = _runtime(tmp_path, devices=("d_one",), app=_echo())
    assert rt._config_overrides["d_one"] == {
        "audio_volume": 0.3, "logging_policy": int(LoggingPolicy.NO_DATA)}
    assert len(_fail_closed_lines(rt)) == 1, list(rt.recent)
    assert not (memdir / "d_one.json").exists() and rt.history.get("d_one") in (None, [])
    drive_turn(rt, "d_one", "a-marker")
    assert not (memdir / "d_one.json").exists(), "the child's words were kept"
    assert rt.memory_store().save("d_one", {"chat": {"facts": ["a-marker"]}}) is False


@pytest.mark.parametrize("save", [{"audio_volume": 0.4}, {"brain": None},
                                  {"logging_policy": 2}])
def test_a_parents_next_save_ends_the_fail_closed_policy(tmp_path, monkeypatch, save):
    """Fail-closed lasts until a parent saves this robot's settings. That save decides
    data sharing again (its own `logging_policy`, else the layer underneath) and rewrites
    the record, so the next start reads it with no warning. Until then the record stays
    exactly as found, so a restart fails closed again, and Be Moxie, which is not a
    setting a parent saves, does not end it."""
    from moxie_sdk.cloud_config import LoggingPolicy
    from moxie_runtime.constants import MEMORY_POLICY
    monkeypatch.delenv("MOXIE_APP", raising=False)
    path = _record(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    rt = _runtime(tmp_path, devices=("d_one",))
    rt.telehealth_enable("d_one", True)
    assert rt.memory_policy("d_one") == LoggingPolicy.NO_DATA
    assert path.read_text() == "{not json"
    again = _runtime(tmp_path, devices=("d_one",))          # a restart before any save
    assert again.memory_policy("d_one") == LoggingPolicy.NO_DATA
    assert len(_fail_closed_lines(again)) == 1

    rt.update_config("d_one", **save)                       # the parent saves
    decided = LoggingPolicy(save.get("logging_policy", MEMORY_POLICY))
    assert rt.memory_policy("d_one") == decided
    assert json.loads(path.read_text()) == save
    fresh = _runtime(tmp_path, devices=("d_one",))
    assert fresh._config_overrides["d_one"] == save
    assert fresh.memory_policy("d_one") == decided
    assert _fail_closed_lines(fresh) == []


def test_a_stored_value_the_whitelist_now_refuses_is_dropped_at_load(tmp_path, capsys):
    """A hand edit, a value from another build, or a key the console never offers is
    re-checked by the console's own whitelist, dropped on its own (the rest still loads),
    never pushed, and named in one line."""
    path = _record(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"audio_volume": 0.3, "audio_wake_set": "loud",
                                "weekday_bedtime": ["25:00", "07:00"], "moxie_mode": 1,
                                "not_a_setting": True}))
    rt = _runtime(tmp_path, devices=("d_one",))
    assert rt._config_overrides["d_one"] == {"audio_volume": 0.3}
    cfg = rt._push_config("d_one")
    assert cfg["audio_volume"] == 0.3
    assert (cfg["audio_wake_set"], cfg["weekday_bedtime_enabled"], cfg["moxie_mode"]) == \
        ("off", False, "DEFAULT_MODE")
    said = [ln for ln in capsys.readouterr().out.splitlines() if "dropped" in ln]
    assert len(said) == 1, said
    for key in ("audio_wake_set", "weekday_bedtime", "moxie_mode", "not_a_setting"):
        assert key in said[0]


def test_a_hand_edited_value_is_canonicalized_at_load_never_pushed_raw(tmp_path, capsys):
    """Saves are always canonical, so only a hand edit reaches this. The whitelist's own
    canonical value is what loads: `audio_volume: 30` (the console's 0-100 slider) comes
    back as 0.3, not as a volume of 30 pushed to the robot, and a data-sharing choice
    written by name comes back as the number every policy reader resolves, rather than
    a string they cannot read (which would quietly mean the default)."""
    from moxie_sdk.cloud_config import LoggingPolicy
    path = _record(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"audio_volume": 30, "screen_brightness": 50,
                                "logging_policy": "NO_DATA", "audio_wake_set": "ON"}))
    rt = _runtime(tmp_path, devices=("d_one",))
    assert rt._config_overrides["d_one"] == {
        "audio_volume": 0.3, "screen_brightness": 0.5,
        "logging_policy": int(LoggingPolicy.NO_DATA), "audio_wake_set": "on"}
    cfg = rt._push_config("d_one")
    assert (cfg["audio_volume"], cfg["screen_brightness"], cfg["audio_wake_set"]) == \
        (0.3, 0.5, "on")
    assert rt.safety_policy("d_one") == LoggingPolicy.NO_DATA
    assert "dropped" not in capsys.readouterr().out


def test_a_brain_the_current_pin_refuses_is_dropped_at_load(tmp_path, monkeypatch):
    """`MOXIE_APP` is the operator's statement about the box (brain-picker.md), so a pick
    saved before the pin is not restored under it. Loading writes nothing: a later boot
    whose pin allows the pick again still finds it."""
    monkeypatch.delenv("MOXIE_APP", raising=False)
    rt = _runtime(tmp_path, devices=("d_one",))
    rt.update_config("d_one", brain="webhook", audio_volume=0.4)
    monkeypatch.setenv("MOXIE_APP", "echo")
    assert _runtime(tmp_path, devices=("d_one",))._config_overrides["d_one"] == \
        {"audio_volume": 0.4}
    for allows_it in ("any", "webhook"):
        monkeypatch.setenv("MOXIE_APP", allows_it)
        assert _runtime(tmp_path, devices=("d_one",))._config_overrides["d_one"] == \
            {"brain": "webhook", "audio_volume": 0.4}


def test_clearing_a_value_is_remembered_across_a_restart(tmp_path):
    """`null` is a setting too ("no bedtime for this robot"); after a restart it must not
    turn back into the house bedtime."""
    rt = _runtime(tmp_path, devices=("d_one",))
    rt.update_fleet_config(weekday_bedtime=["20:00", "07:00"])
    rt.update_config("d_one", weekday_bedtime=["19:30", "07:00"], brain="webhook")
    rt.update_config("d_one", weekday_bedtime=None, brain=None)
    fresh = _runtime(tmp_path, devices=("d_one",))
    assert fresh._config_overrides["d_one"] == {"weekday_bedtime": None, "brain": None}
    assert fresh._push_config("d_one")["weekday_bedtime_enabled"] is False


def test_be_moxie_mode_is_not_saved_so_a_restart_hands_the_robot_back_its_brain(tmp_path):
    """Puppet mode belongs to a live operator session, which is RAM-only. A restart must
    not leave a child's robot waiting on an operator who is gone."""
    rt = _runtime(tmp_path, devices=("d_one",))
    rt.update_config("d_one", audio_volume=0.5)
    rt.telehealth_enable("d_one", True)
    assert rt._config_overrides["d_one"]["moxie_mode"] == 1
    fresh = _runtime(tmp_path, devices=("d_one",))
    assert fresh._config_overrides["d_one"] == {"audio_volume": 0.5}
    assert fresh._push_config("d_one")["moxie_mode"] == "DEFAULT_MODE"


def test_a_save_the_store_refuses_still_applies_now_and_says_it_was_not_saved(tmp_path):
    """A full disk or a read-only volume: the edit still reaches the robot, and the
    console's activity feed says it will not survive a restart rather than staying quiet."""
    class _Refusing(JsonStore):
        def _write_path(self, path, value):
            if path == self.path("d_one", "config"):
                return False
            return super()._write_path(path, value)

    rt = _runtime(tmp_path, devices=("d_one",), store=_Refusing(root=str(tmp_path)))
    rt.update_config("d_one", audio_volume=0.2)
    assert _pushed(rt, "d_one")["audio_volume"] == 0.2
    assert not _record(tmp_path).exists()
    assert any("d_one" in n["text"] and "NOT saved" in n["text"] for n in rt.recent)


def test_two_edits_of_one_robot_reach_the_disk_in_the_order_they_changed_ram(tmp_path):
    """Two edits of one robot at once (a threaded status server, an operator's script):
    each changes RAM, snapshots it and writes the snapshot. Unless the record is held
    across all three, the first edit can write its OLDER snapshot after the second, and
    the next restart brings back a volume the parent already changed. Edit A is paused
    between its snapshot and its write; edit B runs in that gap; the file must end up
    equal to RAM. Deterministic: no sleeps, every wait is on an event."""
    import threading
    rt = _runtime(tmp_path, devices=("d_one",))
    a_paused, release_a, b_progress = (threading.Event() for _ in range(3))
    real_write, real_tx = rt.store.write, rt.store.transaction

    def write(device_id, collection, value):
        if threading.current_thread().name == "edit-A" and collection == "config":
            a_paused.set()                       # A has its snapshot and has not written
            assert release_a.wait(10), "edit A was never released"
        return real_write(device_id, collection, value)

    def transaction(device_id, collection):
        if threading.current_thread().name == "edit-B":
            b_progress.set()                     # B is at the record, which A holds
        return real_tx(device_id, collection)

    def edit_b():
        rt.update_config("d_one", audio_volume=0.5)
        b_progress.set()                         # B is done (nothing made it wait)

    rt.store.write, rt.store.transaction = write, transaction
    a = threading.Thread(target=rt.update_config, args=("d_one",),
                         kwargs={"audio_volume": 0.3}, name="edit-A", daemon=True)
    b = threading.Thread(target=edit_b, name="edit-B", daemon=True)
    a.start()
    assert a_paused.wait(10), "edit A never reached its write"
    b.start()
    assert b_progress.wait(10), "edit B neither finished nor reached the record"
    release_a.set()
    a.join(10)
    b.join(10)
    assert not a.is_alive() and not b.is_alive()
    assert rt._config_overrides["d_one"] == {"audio_volume": 0.5}
    assert json.loads(_record(tmp_path).read_text()) == {"audio_volume": 0.5}, \
        "the file holds the older edit: a restart would undo the newer one"


def test_a_default_store_runtime_keeps_its_records_in_this_tests_own_data_dir(
        isolated_data_dir):
    """`make_runtime(store=None)` builds on `JsonStore()`, that is `MOXIE_DATA_DIR`, and a
    runtime reads every robot's saved settings there when it is built. With one data dir
    for the whole session a record outlived its test (three did, measured), so a `NO_DATA`
    set here on `d_test` would have put every later default-store `d_test` under NO_DATA.
    conftest's `per_test_data_dir` gives each test its own."""
    pytest.importorskip("paho.mqtt.client", reason="the runtime imports paho")
    from helpers_runtime import make_runtime
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.cloud_config import LoggingPolicy

    class _App(MoxieApp):
        name = "content"

    rt, did = make_runtime(_App())
    rt.update_config(did, logging_policy=int(LoggingPolicy.NO_DATA))
    mine = os.path.join(os.environ["MOXIE_DATA_DIR"], "robots", did, "config.json")
    assert os.path.exists(mine)
    assert not os.path.exists(os.path.join(isolated_data_dir, "robots", did, "config.json"))
