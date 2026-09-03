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


def test_normalize_voice_carries_the_environments_pin_to_the_card():
    """A short dropdown needs its reason travelling with it. The supervisor filtered the
    list because `MOXIE_TTS` pinned the engine; if the note were dropped here, the card
    would look like a gateway that had lost half its voices."""
    from moxie_server.fleet import normalize_voice
    payload = _voice_payload()
    payload["pins"] = {"speech": "piper", "listening": ""}
    payload["pin_notes"] = {"speech": "MOXIE_TTS=piper pins the voice to local Piper; "
                                      "only its entries are offered here.",
                            "listening": ""}
    v = normalize_voice(payload)
    assert v["pins"] == {"speech": "piper", "listening": ""}
    assert "MOXIE_TTS=piper" in v["pin_notes"]["speech"]
    assert v["pin_notes"]["listening"] == ""
    # An older supervisor sends neither field; the card must still render.
    plain = normalize_voice(_voice_payload())
    assert plain["pins"] == {"speech": "", "listening": ""}
    assert plain["pin_notes"] == {"speech": "", "listening": ""}


def test_normalize_voice_drops_an_option_with_no_id():
    from moxie_server.fleet import normalize_voice
    payload = _voice_payload()
    payload["available"]["speech"].append({"label": "a voice with no id"})
    assert len(normalize_voice(payload)["available"]["speech"]) == 2


# ---------------------------------------------------------------------------
# Durable telemetry → the 📈 card, and the three console actions
# ---------------------------------------------------------------------------
# `normalize_telemetry` grew a history/retention/policy half when telemetry became
# durable, and three new pure helpers landed for the endpoints that used to report
# success for nothing. All pure, so they belong in this hermetic file.

def _durable_payload(**over):
    """A runtime `GET /telemetry` body of the durable shape."""
    payload = {
        "ok": True, "device_id": "d_abc", "connected": True,
        "summary": {"count": 2, "by_event": {"wake": 2}, "last_seen": {"wake": 140},
                    "latest": [{"event_name": "wake", "recorded_at": 140}]},
        "events": [{"event_name": "wake", "recorded_at": 140}],
        "policy": "NO_MEDIA", "persisted": True,
        "retention": {"packets": 500, "days": 35},
        "history": [{"day": "2026-08-31", "count": 4, "top_event": "wake"},
                    {"day": "2026-09-01", "count": 0, "top_event": None},
                    {"day": "2026-09-02", "count": 2, "top_event": "wake"}],
        "totals": {"total": 41, "days_kept": 2, "first_day": "2026-08-31",
                   "last_day": "2026-09-02", "dropped_days": 6, "updated_at": 1},
    }
    payload.update(over)
    return payload


def test_normalize_history_scales_bars_against_the_busiest_day():
    from moxie_server.fleet import normalize_history
    rows = normalize_history([{"day": "2026-09-01", "count": 4, "top_event": "wake"},
                              {"day": "2026-09-02", "count": 1, "top_event": "said"}])
    assert [r["share"] for r in rows] == [1.0, 0.25]
    assert [r["day"] for r in rows] == ["2026-09-01", "2026-09-02"]


def test_normalize_history_keeps_a_zero_day_as_a_zero_day():
    """A quiet day must render as a quiet day, not vanish from the week."""
    from moxie_server.fleet import normalize_history
    rows = normalize_history([{"day": "2026-09-01", "count": 0},
                              {"day": "2026-09-02", "count": 0}])
    assert len(rows) == 2 and all(r["share"] == 0.0 for r in rows)
    assert rows[0]["top_event"] is None


def test_normalize_history_never_raises_on_junk():
    from moxie_server.fleet import normalize_history
    for junk in (None, [], "nope", [None, 7, {}, {"day": ""}],
                 [{"day": "2026-09-02", "count": "x"}]):
        assert isinstance(normalize_history(junk), list)


def test_normalize_telemetry_carries_the_durable_half():
    t = normalize_telemetry(_durable_payload())
    assert t["ok"] is True and t["count"] == 2
    assert [r["count"] for r in t["history"]] == [4, 0, 2]
    assert t["policy"] == "NO_MEDIA" and t["persisted"] is True
    assert t["retention"] == {"packets": 500, "days": 35}
    assert t["totals"]["total"] == 41 and t["totals"]["first_day"] == "2026-08-31"
    assert t["totals"]["dropped_days"] == 6


def test_normalize_telemetry_never_claims_persistence_it_was_not_told_about():
    """A payload from a supervisor that predates durable telemetry (or an error body)
    must not be rendered as if it had a history."""
    t = normalize_telemetry({"ok": True, "device_id": "d", "summary": {"count": 1},
                             "events": []})
    assert t["persisted"] is False and t["history"] == []
    assert t["totals"]["total"] == 0 and t["retention"]["days"] == 0
    down = normalize_telemetry({"ok": False, "error": "supervisor not reachable"})
    assert down["persisted"] is False and down["history"] == []


def test_normalize_telemetry_reports_a_no_data_robot_honestly():
    """Under `NO_DATA` the card must say nothing is kept rather than show an empty week
    as though the robot had been silent."""
    t = normalize_telemetry(_durable_payload(policy="NO_DATA", persisted=False,
                                             history=[], totals={}))
    assert t["persisted"] is False and t["policy"] == "NO_DATA"
    assert t["history"] == [] and t["totals"]["total"] == 0


def test_normalize_telemetry_still_never_raises_on_junk():
    for junk in (None, {}, [], "nope", {"ok": True, "totals": "x", "retention": 7,
                                        "history": "nope"},
                 {"ok": True, "summary": "x", "totals": {"total": "many"},
                  "retention": {"days": "lots"}}):
        t = normalize_telemetry(junk)
        assert set(t["retention"]) == {"packets", "days"}
        assert isinstance(t["history"], list) and isinstance(t["totals"], dict)


# --- the three console actions ---

def test_unsupported_action_is_never_a_fake_success():
    """The bug this shape exists to make impossible: `reboot` used to return
    `{"error": null}` while publishing nothing."""
    from moxie_server.fleet import unsupported_action
    body = unsupported_action("reboot")
    assert body["ok"] is False and body["supported"] is False
    assert body["error"] == "unsupported" and body["action"] == "reboot"
    assert "not something this appliance can do" in body["reason"]
    assert "power-and-system-events.md" in body["evidence"], "no citation for the refusal"


def test_unsupported_action_has_a_reason_even_for_an_unlisted_name():
    from moxie_server.fleet import unsupported_action
    body = unsupported_action("teleport")
    assert body["ok"] is False and "teleport" in body["reason"]


def test_reboot_is_the_only_unsupported_action_and_wakeup_is_not_one():
    """A regression guard with teeth: if someone lists `wakeup` here again, the real
    publish path has been quietly turned back into a no-op."""
    from moxie_server.fleet import UNSUPPORTED_ACTIONS
    assert set(UNSUPPORTED_ACTIONS) == {"reboot"}


def test_ota_status_never_says_up_to_date():
    """This appliance serves no `api/ota`, so "there is nothing newer" is not a claim it
    is in a position to make about anything."""
    from moxie_server.fleet import ota_status_view
    snap = {"ok": True, "robots": [{"device_id": "d_abc", "firmware": "3.6.4",
                                    "ota_reboot_required": False}]}
    for view in (ota_status_view(snap, "d_abc"), ota_status_view(snap),
                 ota_status_view(None), ota_status_view({"ok": False, "robots": []})):
        assert view["status"] != "up_to_date"
        assert view["ota_server"] is False and view["supported"] is False


def test_ota_status_reports_the_firmware_the_robot_actually_told_us():
    from moxie_server.fleet import ota_status_view
    snap = {"ok": True, "robots": [{"device_id": "d_abc", "firmware": "3.6.4",
                                    "ota_reboot_required": False}]}
    view = ota_status_view(snap, "d_abc")
    assert view["status"] == "unknown" and view["version"] == "3.6.4"
    assert view["ota_reboot_required"] is False and view["device_id"] == "d_abc"
    assert "no OTA server" in view["note"]


def test_ota_status_surfaces_the_one_ota_fact_the_protocol_gives_us():
    """`ota_reboot_required` is a real `RobotStatus` field the robot reports up."""
    from moxie_server.fleet import ota_status_view
    snap = {"ok": True, "robots": [{"device_id": "d_abc", "firmware": "3.6.4",
                                    "ota_reboot_required": True}]}
    view = ota_status_view(snap, "d_abc")
    assert view["status"] == "reboot_required" and view["ota_reboot_required"] is True
    assert "holding a reboot" in view["reason"]


def test_ota_status_is_unavailable_rather_than_invented_when_nothing_is_known():
    from moxie_server.fleet import ota_status_view
    snap = {"ok": True, "robots": [{"device_id": "d_other"}]}
    view = ota_status_view(snap, "d_abc")
    assert view["status"] == "unavailable" and view["version"] is None
    assert view["ota_reboot_required"] is None


def test_ota_status_will_not_guess_which_robot_when_several_are_connected():
    from moxie_server.fleet import ota_status_view
    snap = {"ok": True, "robots": [{"device_id": "d_1"}, {"device_id": "d_2"}]}
    assert ota_status_view(snap)["status"] == "unavailable"


# --- resolving a parent-app record to an MQTT identity ---

def test_resolve_device_id_prefers_what_the_record_remembers():
    from moxie_server.fleet import resolve_device_id
    snap = {"ok": True, "robots": [{"device_id": "d_other", "pending": False}]}
    assert resolve_device_id({"mqtt-device-id": "d_mine"}, snap) == ("d_mine", "record")


def test_resolve_device_id_falls_back_to_the_only_served_robot():
    from moxie_server.fleet import resolve_device_id
    snap = {"ok": True, "robots": [{"device_id": "d_only", "pending": False}]}
    assert resolve_device_id({}, snap) == ("d_only", "sole-served")


def test_resolve_device_id_refuses_to_guess_between_two_robots():
    from moxie_server.fleet import resolve_device_id
    snap = {"ok": True, "robots": [{"device_id": "d_1", "pending": False},
                                   {"device_id": "d_2", "pending": False}]}
    assert resolve_device_id({}, snap) == (None, "ambiguous")


def test_resolve_device_id_ignores_a_pending_robot():
    """A robot that has not been permitted is not "the" robot — nothing may be sent to
    it, so it cannot be the implicit target of a button."""
    from moxie_server.fleet import resolve_device_id
    snap = {"ok": True, "robots": [{"device_id": "d_pending", "pending": True}]}
    assert resolve_device_id({}, snap) == (None, "none")
    assert resolve_device_id(None, None) == (None, "none")
