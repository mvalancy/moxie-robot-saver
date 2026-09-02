"""
Fleet-view tests (M6 parent console) — normalize the MQTT supervisor's status snapshot
into the console-facing shape. Pure (no fastapi/network), so it runs in the hermetic
suite; mirrors MoxieRuntime.status_snapshot() → server/local/fleet.
"""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "server"))

from moxie_server.fleet import normalize_fleet, normalize_robot, robot_summary  # noqa: E402


def _snapshot():
    return {
        "ok": True, "app": "content", "uptime_s": 42,
        "robots": [{
            "device_id": "d_abc", "child": "Sam", "firmware": "3.6.4",
            "battery_level": 82, "audio_volume": 5, "wifi_ssid": "Home",
            "mode": "normal", "ota_reboot_required": False,
            "config_overrides": {"bedtime": "20:00"}, "telemetry_count": 3,
        }],
        "recent": [{"t": 1, "kind": "chat", "text": "hi"}],
    }


def test_normalize_fleet_full_snapshot():
    f = normalize_fleet(_snapshot())
    assert f["ok"] and f["app"] == "content" and f["robot_count"] == 1
    r = f["robots"][0]
    assert r["device_id"] == "d_abc" and r["child"] == "Sam" and r["online"] is True
    assert r["battery_level"] == 82 and r["audio_volume"] == 5 and r["wifi_ssid"] == "Home"
    assert r["config_overrides"] == {"bedtime": "20:00"} and r["telemetry_count"] == 3
    assert "battery 82%" in r["summary"] and "Wi-Fi Home" in r["summary"]
    assert f["recent"] == [{"t": 1, "kind": "chat", "text": "hi"}]


def test_normalize_fleet_supervisor_down():
    f = normalize_fleet({"ok": False, "error": "supervisor not reachable",
                         "robots": [], "recent": []})
    assert f["ok"] is False and f["robot_count"] == 0 and f["robots"] == []
    assert f["error"] == "supervisor not reachable"


def test_normalize_fleet_none_is_safe():
    f = normalize_fleet(None)
    assert f["ok"] is False and f["robot_count"] == 0 and f["robots"] == []
    assert f["error"]


def test_normalize_robot_coerces_and_defaults():
    r = normalize_robot({"device_id": "d1", "battery_level": "77", "audio_volume": True})
    assert r["battery_level"] == 77                 # numeric string coerced
    assert r["audio_volume"] is None                # a bool isn't a volume
    assert r["config_overrides"] == {} and r["telemetry_count"] == 0
    assert r["ota_reboot_required"] is False and r["summary"]


def test_robot_summary_flags_ota_and_falls_back():
    assert "OTA reboot pending" in robot_summary({"ota_reboot_required": True})
    assert robot_summary({}) == "connected"         # nothing known → a sane default
