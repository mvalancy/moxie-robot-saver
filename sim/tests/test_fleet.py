"""
Fleet-view tests (M6 parent console) — normalize the MQTT supervisor's status snapshot
into the console-facing shape. Pure (no fastapi/network), so it runs in the hermetic
suite; mirrors MoxieRuntime.status_snapshot() → server/local/fleet.
"""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "server"))

from moxie_server.fleet import (  # noqa: E402
    event_counts, normalize_event, normalize_fleet, normalize_robot,
    normalize_telemetry, robot_summary,
)


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


# --- telemetry / insights view (M6) ---

def _telemetry():
    return {
        "ok": True, "device_id": "d_abc",
        "summary": {"count": 3, "by_event": {"wake": 2, "said": 1},
                    "last_seen": {"wake": 300, "said": 200}},
        "events": [
            {"event_name": "wake", "recorded_at": 300, "moxie_session_id": "s1",
             "model": "Event"},
            {"event_name": "said", "recorded_at": 200, "moxie_session_id": "s1"},
        ],
    }


def test_normalize_telemetry_full_payload():
    t = normalize_telemetry(_telemetry())
    assert t["ok"] and t["device_id"] == "d_abc" and t["count"] == 3 and t["error"] is None
    assert t["by_event"] == [{"event": "wake", "count": 2, "last_seen": 300},
                             {"event": "said", "count": 1, "last_seen": 200}]
    assert t["events"][0] == {"event_name": "wake", "recorded_at": 300,
                              "session_id": "s1", "model": "Event"}


def test_normalize_telemetry_no_events_yet():
    t = normalize_telemetry({"ok": True, "device_id": "d1",
                             "summary": {"count": 0, "by_event": {}, "last_seen": {}},
                             "events": []})
    assert t["ok"] and t["count"] == 0 and t["by_event"] == [] and t["events"] == []


def test_normalize_telemetry_error_and_none_are_safe():
    down = normalize_telemetry(None)
    assert down["ok"] is False and down["count"] == 0 and down["error"]
    unknown = normalize_telemetry({"ok": False, "device_id": "d_x",
                                   "error": "unknown device_id 'd_x'"})
    assert unknown["ok"] is False and unknown["error"] == "unknown device_id 'd_x'"
    assert unknown["events"] == [] and unknown["by_event"] == []


def test_event_counts_sorted_by_frequency_then_name():
    rows = event_counts({"by_event": {"b": 1, "a": 1, "c": 5}, "last_seen": {"c": 9}})
    assert [r["event"] for r in rows] == ["c", "a", "b"]     # count desc, then name
    assert rows[0]["last_seen"] == 9 and rows[1]["last_seen"] is None


def test_normalize_event_tolerates_a_partial_packet():
    e = normalize_event({})
    assert e["event_name"] == "event" and e["recorded_at"] is None and e["session_id"] == ""
    assert normalize_event({"recorded_at": "77"})["recorded_at"] == 77


# --- 🎚️ the voice picker's normalizer (backlog/voice-picker.md) ----------------------
# The console keeps no list of voices: it renders exactly what the supervisor offers. So
# the only job here is to make that payload *renderable no matter what arrives* — a card
# that 500s or blanks itself is worse than one that prints the reason.

def _voice_payload():
    return {
        "ok": True,
        "available": {
            "speech": [{"id": "gateway:piper-amy", "engine": "gateway",
                        "model": "piper-amy", "group": "Gateway",
                        "label": "Amy (gateway, piper-amy)", "default": True},
                       {"id": "tone", "engine": "tone", "model": "", "group": "Built-in",
                        "label": "Tone (built-in)", "default": False}],
            "listening": [{"id": "off", "engine": "off", "model": "", "group": "Built-in",
                           "label": "Off (built-in)", "default": True}],
        },
        "selected": {"speech": "gateway:piper-amy", "listening": "off"},
        "labels": {"speech": "Amy (gateway, piper-amy)", "listening": "Off (built-in)"},
        "installed": {"speech": "openai-voice (standby: tone)", "listening": ""},
        "chosen": {"speech": True, "listening": False},
        "discovering": False, "gateway_error": "", "updated_at": 1788400000,
        "robots": ["d_abc"],
    }


def test_normalize_voice_keeps_every_field_the_card_renders():
    from moxie_server.fleet import normalize_voice
    v = normalize_voice(_voice_payload())
    assert v["ok"] is True and v["error"] is None
    assert [e["id"] for e in v["available"]["speech"]] == ["gateway:piper-amy", "tone"]
    assert v["available"]["speech"][0]["default"] is True
    assert v["selected"] == {"speech": "gateway:piper-amy", "listening": "off"}
    assert v["installed"]["speech"] == "openai-voice (standby: tone)"
    assert v["chosen"] == {"speech": True, "listening": False}
    assert v["updated_at"] == 1788400000 and v["robots"] == ["d_abc"]


def test_normalize_voice_reports_a_gateway_outage_without_blanking_the_card():
    from moxie_server.fleet import normalize_voice
    payload = _voice_payload()
    payload["gateway_error"] = "APIConnectionError"
    v = normalize_voice(payload)
    assert v["ok"] is True and v["gateway_error"] == "APIConnectionError"
    assert len(v["available"]["speech"]) == 2, "an outage must not empty the dropdown"


def test_normalize_voice_carries_a_refusals_reason():
    from moxie_server.fleet import normalize_voice
    v = normalize_voice({"ok": False, "error": "bad pick",
                         "reason": "'gateway:x' is not one of this appliance's options."})
    assert v["ok"] is False and "not one of" in v["reason"]
    assert v["error"] == "bad pick" and v["available"] == {"speech": [], "listening": []}


def test_normalize_voice_never_raises_on_junk():
    from moxie_server.fleet import normalize_voice
    for junk in (None, {}, [], "nope", {"available": "not a dict"},
                 {"ok": True, "available": {"speech": "abc"}},
                 {"ok": True, "selected": 7, "updated_at": "soon"}):
        v = normalize_voice(junk)
        assert set(v["available"]) == {"speech", "listening"}
        assert isinstance(v["available"]["speech"], list)
        assert isinstance(v["selected"]["speech"], str)
        assert isinstance(v["updated_at"], int)


def test_normalize_voice_drops_an_option_with_no_id():
    from moxie_server.fleet import normalize_voice
    payload = _voice_payload()
    payload["available"]["speech"].append({"label": "a voice with no id"})
    assert len(normalize_voice(payload)["available"]["speech"]) == 2
