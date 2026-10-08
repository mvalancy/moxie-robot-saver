"""
Integration: a turn round-trips through the REAL MoxieRuntime pipeline with a fake MQTT
transport (no broker) — `_on_remote_chat → _handle_turn → _publish_chat →
build_chat_response → client.publish` — plus the runtime's other robot-facing seams
(STT frames, config push, `/state`, telemetry, activity-log queries, the day plan) and
the localhost status server the parent console reads.
"""
import base64
import datetime
import json
import os
import urllib.error
import urllib.request

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

from moxie_sdk.app import MoxieApp                       # noqa: E402
from moxie_sdk.store import JsonStore                    # noqa: E402
from moxie_sdk.types import Reply, Action, ActionType, RobotContext, ChildProfile  # noqa: E402
import moxie_runtime                                     # noqa: E402
from helpers_runtime import FakeClient, free_port        # noqa: E402


class _ActionApp(MoxieApp):
    name = "test-action"

    def respond(self, turn):
        return Reply(text=f"You said: {turn.speech}",
                     actions=[Action(type=ActionType.LAUNCH, module_id="DRAW",
                                     content_id="default")])


class _OfflineApp(MoxieApp):
    name = "test-offline"

    def respond(self, turn):
        return Reply.offline()


def _rt(did=None, *, app=None, nickname=None, tmp_path=None, **kw):
    """A real runtime with a fake transport; `did` places one connected robot."""
    child = ChildProfile(nickname=nickname) if nickname else ChildProfile()
    rt = moxie_runtime.MoxieRuntime(app=app or _ActionApp(), child=child,
                                    store=JsonStore(str(tmp_path)) if tmp_path else None,
                                    **kw)
    rt.client = FakeClient()
    if did:
        rt.robots[did] = RobotContext(device_id=did, child=rt.child)
    return rt


def _turn(rt, did, speech, event_id="e", **extra):
    rt._on_remote_chat(did, rt.robots[did], json.dumps(
        {"command": "prompt", "event_id": event_id, "speech": speech, **extra}))
    rt._pool.shutdown(wait=True)                          # flush the turn off the pool
    return rt.client.published


def _on(published, topic):
    return [p for (t, p) in published if t == topic]


def _drive(app, device_id="d_test", speech="hello", synth=None):
    rt = _rt(device_id, app=app, nickname="Sam")
    if synth is not None:
        rt.set_synthesizer(synth)                         # server-side voice for the SIM
    return _turn(rt, device_id, speech, event_id="evt-9", backend="router")


def _chat(published, device_id="d_test"):
    msgs = _on(published, f"/devices/{device_id}/commands/remote_chat")
    assert msgs, f"no remote_chat published; got {published}"
    return msgs[-1]


def _no_tts(published):
    return not [t for (t, _) in published if t.endswith("/commands/tts")]


def _http_get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return json.loads(r.read().decode())


def _http_404(port, path):
    with pytest.raises(urllib.error.HTTPError) as caught:
        _http_get(port, path)
    assert caught.value.code == 404, "unknown device should 404"
    return caught.value


# --------------------------------------------------------------------------- #
# A chat turn
# --------------------------------------------------------------------------- #

def test_turn_roundtrips_text_actions_and_success():
    resp = _chat(_drive(_ActionApp(), speech="let's draw"))
    assert resp["command"] == "remote_chat"
    assert resp["result"] == "SUCCESS"
    assert resp["output"]["text"] == "You said: let's draw"
    assert resp["output"]["markup"]                       # markup auto-generated
    ra = resp["response_actions"]
    assert len(ra) == 1 and ra[0]["action"] == "launch"
    assert ra[0]["module_id"] == "DRAW" and ra[0]["content_id"] == "default"


def test_turn_publishes_decodable_tts_only_when_synth_set(device_id="d_test"):
    """With a server voice installed, a turn also publishes a CloudTTSResponse the SIM
    can decode back to audio — the runtime→SIM voice contract."""
    from moxie_sdk.tts import PiperSynthesizer, decode_cloud_tts_response
    synth = PiperSynthesizer("x.onnx", voice_fn=lambda t: b"AUDIO", sample_rate=22050)
    published = _drive(_ActionApp(), speech="hello", synth=synth)
    tts = _on(published, f"/devices/{device_id}/commands/tts")
    assert tts, f"no tts published; got topics {[t for (t, _) in published]}"
    spoken = decode_cloud_tts_response(tts[-1])
    assert spoken["audio"] == b"AUDIO" and spoken["sample_rate"] == 22050
    assert spoken["event_id"] == "evt-9"                  # carries the turn's event id
    assert _no_tts(_drive(_ActionApp(), speech="hello"))  # the robot self-synthesizes


def test_offline_brain_signals_error_offline_over_the_wire():
    assert _chat(_drive(_OfflineApp()))["result"] == "ERROR_OFFLINE"   # local fallback


def test_content_module_runs_through_the_runtime():
    """A shipped content module, driven by ContentApp inside the real runtime, produces
    the module's reply on the wire."""
    from moxie_sdk.content import load_modules, ContentApp
    with open(os.path.join(REPO, "mqtt", "content_modules", "starter.json")) as fh:
        module = load_modules(json.load(fh))
    rt = _rt(app=ContentApp(module, lambda messages: "Dinosaurs are amazing!"),
             nickname="Sam")
    did = "d_content"
    rt.robots[did] = RobotContext(device_id=did, child=rt.child,
                                  module_id="FREE_CHAT", content_id="default")
    resp = _chat(_turn(rt, did, "tell me about dinosaurs"), did)
    assert resp["result"] == "SUCCESS"
    assert resp["output"]["text"] == "Dinosaurs are amazing!"


def test_history_accumulates_across_the_pipeline():
    rt = _rt("d_hist")
    _turn(rt, "d_hist", "hi")
    h = rt.history.get("d_hist", [])
    assert {"role": "user", "content": "hi"} in h
    assert any(m["role"] == "assistant" for m in h)


# --------------------------------------------------------------------------- #
# Speech-to-text frames
# --------------------------------------------------------------------------- #

def _stt_rt(text_fn):
    from moxie_sdk.stt import Transcriber

    class _Fake(Transcriber):
        def transcribe(self, pcm, sample_rate=16000):
            return text_fn(pcm)

    rt = _rt()
    rt.set_transcriber(_Fake())
    return rt


def _final(rt, did):
    """The last `commands/zmq` publish, parsed with the committed `zmqSTTResponse` oracle:
    the robot reads `b'<full_name>:' + protobuf`, never JSON (test_stt_wire.py)."""
    from helpers_runtime import parse_zmq_frame, toolkit_pb2
    pb = toolkit_pb2("embodied.perception.audio.zmqSTT_pb2")
    msgs = _on(rt.client.published, f"/devices/{did}/commands/zmq")
    assert msgs, "no zmqSTTResponse published"
    return parse_zmq_frame(msgs[-1], pb.zmqSTTResponse)


def test_stt_frames_through_runtime_publish_transcript():
    """VAD frames accumulate and, on END_OF_SPEECH, publish a FINAL zmqSTTResponse frame."""
    rt = _stt_rt(lambda pcm: f"heard {len(pcm)}b")
    did = "d_stt"
    assert rt.feed_stt(did, 1, b"aa", uuid="u1") is None        # START_OF_SPEECH
    assert rt.feed_stt(did, 2, b"bb") is None                    # SPEECH
    assert rt.feed_stt(did, 3, b"cc") == "heard 6b"              # END_OF_SPEECH
    final = _final(rt, did)
    assert final.type == final.FINAL
    assert final.speech == "heard 6b" and final.uuid == "u1"


def test_handle_zmq_json_audio_frame_drives_stt():
    """events/zmq → handle_zmq JSON bridge → feed_stt → published transcript."""
    rt = _stt_rt(lambda pcm: "hello moxie")
    did = "d_zmq"
    a = base64.b64encode(b"xy").decode()
    rt.handle_zmq(did, json.dumps({"vad": 1, "audio_content": a, "uuid": "u9"}))
    assert rt.handle_zmq(did, json.dumps({"vad": 3, "audio_content": a, "uuid": "u9"})) \
        == "hello moxie"
    assert _final(rt, did).speech == "hello moxie"


def test_handle_zmq_real_protobuf_frame_drives_stt():
    """A real robot's protobuf zmqSTTRequest frame off events/zmq → transcript."""
    from helpers_audio import pb_zmq_stt_frame as _frame
    rt = _stt_rt(lambda pcm: f"pb {len(pcm)}b")
    did = "d_pb"
    rt.handle_zmq(did, _frame(1, b"aa", "u5"))                    # START
    assert rt.handle_zmq(did, _frame(3, b"bb", "u5")) == "pb 4b"   # END → transcribe
    final = _final(rt, did)
    assert final.speech == "pb 4b" and final.uuid == "u5"


def test_no_transcriber_ignores_audio():
    rt = _rt()
    assert rt.feed_stt("d", 3, b"aa") is None            # no transcriber → no-op
    assert rt.client.published == []


# --------------------------------------------------------------------------- #
# Config push, /state, the console snapshot
# --------------------------------------------------------------------------- #

def test_push_config_publishes_spec_robot_cloud_config():
    """`_push_config` emits a spec-conformant RobotCloudConfig for a **permitted** robot;
    the unpermitted shape is `test_device_permits.py`."""
    rt = _rt(nickname="Sam", allow_unverified_bots=True)
    rt._push_config("d_cfg")
    msgs = _on(rt.client.published, "/devices/d_cfg/config")
    assert msgs, "no config published"
    cfg = msgs[-1]
    assert cfg["pairing_status"] == "paired"                 # the wrapper the robot needs
    assert cfg["child_pii"]["nickname"] == "Sam"             # the runtime's child, not a default
    assert cfg["data_sharing"] == "NO_DATA"                  # LoggingPolicy default


def test_state_ingest_stores_robot_status():
    rt = _rt("d_state")
    rt._on_state("d_state", json.dumps({"robot_firmware_version": "v24.10.803",
                                        "battery_level": 0.9, "wifi_ssid": "home"}))
    assert rt.robots["d_state"].firmware == "v24.10.803"
    assert rt.robots["d_state"].extra["status"]["battery_level"] == 0.9


def test_update_config_republishes_with_merged_overrides():
    """Parent edits re-publish the RobotCloudConfig; overrides merge and persist."""
    rt = _rt(nickname="Sam", allow_unverified_bots=True)
    did = "d_upd"
    rt.update_config(did, audio_volume=0.9, timezone_id="America/New_York")
    cfg = _on(rt.client.published, f"/devices/{did}/config")[-1]
    assert cfg["audio_volume"] == 0.9 and cfg["timezone_id"] == "America/New_York"
    assert cfg["child_pii"]["nickname"] == "Sam"          # base config intact
    rt.update_config(did, screen_brightness=0.5)           # a second edit merges
    cfg2 = _on(rt.client.published, f"/devices/{did}/config")[-1]
    assert cfg2["audio_volume"] == 0.9 and cfg2["screen_brightness"] == 0.5


def test_status_snapshot_surfaces_robot_state():
    did = "d_snap"
    rt = _rt(did, nickname="Sam")
    rt._on_state(did, json.dumps({"robot_firmware_version": "v24.10.803",
                                  "battery_level": 0.77, "wifi_ssid": "home", "mode": "idle"}))
    rt.update_config(did, audio_volume=0.8)
    snap = rt.status_snapshot()
    assert snap["ok"] and snap["app"]
    r = [x for x in snap["robots"] if x["device_id"] == did][0]
    assert r["battery_level"] == 0.77 and r["wifi_ssid"] == "home" and r["mode"] == "idle"
    assert r["firmware"] == "v24.10.803"
    assert r["config_overrides"]["audio_volume"] == 0.8


# --------------------------------------------------------------------------- #
# Telemetry and the status server
# --------------------------------------------------------------------------- #

def _packet(did, name, **kw):
    from moxie_sdk.telemetry import build_packet
    return json.dumps(build_packet(name, kw.pop("data", b""), moxie_id=did, **kw))


def test_telemetry_ingest_stores_and_counts():
    did = "d_tel"
    rt = _rt(did)
    rt._on_event(did, "telemetry", _packet(did, "wake", data=b"x"))
    rt._on_event(did, "telemetry", _packet(did, "said", data="hi"))
    assert len(rt.robots[did].extra["telemetry"]) == 2
    snap = [r for r in rt.status_snapshot()["robots"] if r["device_id"] == did][0]
    assert snap["telemetry_count"] == 2


def test_telemetry_view_summarizes_stored_packets():
    """What GET /telemetry serves: packets rolled up by event, newest first."""
    did = "d_view"
    rt = _rt(did)
    for name, ts in (("wake", 100), ("said", 200), ("wake", 300)):
        rt.ingest_telemetry(did, _packet(did, name, recorded_at=ts))
    view = rt.telemetry_view(did)
    assert view["ok"] and view["device_id"] == did
    assert view["summary"]["count"] == 3
    assert view["summary"]["by_event"] == {"wake": 2, "said": 1}
    assert view["summary"]["last_seen"]["wake"] == 300
    assert [e["event_name"] for e in view["events"]] == ["wake", "said", "wake"]


def test_telemetry_view_honors_limit_and_unknown_device():
    did = "d_lim"
    rt = _rt(did)
    for i in range(4):
        rt.ingest_telemetry(did, _packet(did, f"e{i}"))
    assert len(rt.telemetry_view(did, limit=2)["events"]) == 2
    missing = rt.telemetry_view("d_nope")
    assert missing["ok"] is False and "unknown device_id" in missing["error"]


def test_status_server_serves_status_and_telemetry():
    """GET /status and GET /telemetry (404 for an unknown device)."""
    did = "d_http"
    rt = _rt(did)
    rt.ingest_telemetry(did, _packet(did, "wake", recorded_at=42))
    port = free_port()
    rt._start_status_server(port)

    assert _http_get(port, "/status")["ok"] is True
    view = _http_get(port, f"/telemetry?device_id={did}&limit=5")
    assert view["ok"] and view["summary"]["by_event"] == {"wake": 1}
    assert view["events"][0]["recorded_at"] == 42
    err = _http_404(port, "/telemetry?device_id=d_missing")
    assert json.loads(err.read().decode())["ok"] is False


# --------------------------------------------------------------------------- #
# Activity-log queries (client-service-activity-log → commands/query_result)
# --------------------------------------------------------------------------- #

def _activity_runtime(device_id="d_test", tmp_path=None):
    return _rt(device_id, tmp_path=tmp_path)


def _activity(rt, payload, device_id="d_test"):
    """Push one client-service-activity-log event through the REAL event router."""
    rt._on_event(device_id, "client-service-activity-log", json.dumps(payload))
    return rt.client.published


def _drive_activity(payload, device_id="d_test"):
    return _activity(_activity_runtime(device_id), payload, device_id)


def _query(rt, query, device_id, request_id="r"):
    return _activity(rt, {"subtopic": "query", "query": query, "request_id": request_id},
                     device_id)[0][1]


@pytest.mark.parametrize("query,key,want", [
    ("schedule", "schedule", None), ("mentor_behaviors", "mentor_behaviors", []),
    ("license", "license_values", []),
])
def test_a_query_is_answered_on_query_result_echoing_its_request_id(query, key, want):
    pub = _drive_activity({"subtopic": "query", "query": query, "request_id": "req-1"})
    assert len(pub) == 1
    topic, msg = pub[0]
    assert topic == "/devices/d_test/commands/query_result"
    assert (msg["command"], msg["query"]) == ("query_result", query)
    assert msg["request_id"] == "req-1"            # the robot correlates on this
    assert key in msg and "result" not in msg      # the old generic key is gone
    if want is not None:
        assert msg[key] == want                    # nothing reported yet


def test_schedule_query_serves_a_real_nonempty_day_plan(device_id="d_plan"):
    """The robot will not enter a session without a schedule, so the payload must be a
    well-formed, non-empty ContentSchedule, not `{}`."""
    from moxie_sdk.schedule import validate_schedule
    sched = _query(_activity_runtime(device_id), "schedule", device_id)["schedule"]
    assert validate_schedule(sched) == []
    ids = [r["module_id"] for r in sched["provided_schedule"]]
    assert len(ids) >= 8 and "DM" in ids
    assert "generate" not in sched                 # authoring key never hits the wire


def test_query_without_subtopic_is_still_answered():
    # looser senders omit `subtopic`; a bare `query` must not go unanswered
    _, msg = _drive_activity({"query": "schedule", "request_id": "req-bare"})[0]
    assert msg["query"] == "schedule" and msg["request_id"] == "req-bare"


def test_non_query_activity_subtopics_publish_nothing():
    # a mentor_behavior *report* and telehealth state are not query_result traffic
    assert _drive_activity({"mentor_behavior": {"module_id": "DM"}}) == []
    assert _drive_activity({"subtopic": "telehealth", "message": {"state": "idle"}}) == []


# ---- mentor behaviors: report → store → serve ----

_REPORT = {"timestamp": 1725000000000, "software_version": "24.10.803",
           "module_name": "robotbrain",
           "mentor_behavior": {"module_id": "DM", "content_id": "mission_1",
                               "content_day": "3", "timestamp": 1725000000000,
                               "action": "COMPLETED", "instance_id": 41,
                               "ended_reason": "MOXIE_ENDED"}}


def _completed(module_id, timestamp, **kw):
    return {"mentor_behavior": {"module_id": module_id, "timestamp": timestamp,
                                "action": "COMPLETED", **kw}}


def test_mentor_behavior_report_is_stored_and_served_back(tmp_path, device_id="d_mbh"):
    """The robot reports a finished activity (ActivityUpdate.mentor_behavior), the runtime
    stores it, and the next `mentor_behaviors` query serves it."""
    rt = _activity_runtime(device_id, tmp_path)
    assert _activity(rt, _REPORT, device_id) == []          # a report answers nothing
    msg = _query(rt, "mentor_behaviors", device_id, "r2")
    assert msg["mentor_behaviors"] == [_REPORT["mentor_behavior"]]
    # envelope fields are report metadata, not history
    assert "software_version" not in msg["mentor_behaviors"][0]


def test_stored_behaviors_survive_a_supervisor_restart(tmp_path, device_id="d_restart"):
    _activity(_activity_runtime(device_id, tmp_path), _REPORT, device_id)
    rt2 = _activity_runtime(device_id, tmp_path)            # "restart"
    assert rt2.mentor_behaviors(device_id) == [_REPORT["mentor_behavior"]]


def test_reported_behaviors_are_served_newest_first(tmp_path, device_id="d_order"):
    rt = _activity_runtime(device_id, tmp_path)
    for ts in (100, 300, 200):
        _activity(rt, _completed(f"M{ts}", ts), device_id)
    assert [r["module_id"] for r in rt.mentor_behaviors(device_id)] == \
        ["M300", "M200", "M100"]


def test_a_useless_report_is_ignored(tmp_path, device_id="d_junk"):
    rt = _activity_runtime(device_id, tmp_path)
    assert rt.ingest_mentor_behavior(device_id, {"mentor_behavior": {}}) is None
    assert rt.ingest_mentor_behavior(device_id, {"mentor_behavior": {"action": "QUIT"}}) \
        is None                                             # no module_id → unusable
    assert rt.mentor_behaviors(device_id) == []


def test_reported_behaviors_shape_the_next_days_schedule(tmp_path, device_id="d_ftue"):
    """Once onboarding is reported complete the schedule stops serving it, so FTUE ends."""
    rt = _activity_runtime(device_id, tmp_path)
    for i in range(9):
        _activity(rt, _completed("TNT", 1000 + i, content_id=f"c{i}"), device_id)
    for i in range(4):
        _activity(rt, _completed("SYSTEMSCHECK", 2000 + i, content_id=f"c{i}"), device_id)
    rt.client.published.clear()
    msg = _query(rt, "schedule", device_id)
    ids = [r["module_id"] for r in msg["schedule"]["provided_schedule"]]
    assert not ({"TNT", "SYSTEMSCHECK", "WELCOME"} & set(ids)), ids
    assert ids, "the day must not be empty once onboarding is done"


def test_schedule_uses_the_running_content_modules_schedules_block(tmp_path):
    """A ContentApp's `schedules[]` is the authoring surface the served plan comes from."""
    from moxie_sdk.content import load_modules, ContentApp
    module = load_modules({"schedules": [
        {"name": "quiet", "schedule": {"provided_schedule": [{"module_id": "AUDMED"}]}}]})
    rt = _rt("d_authored", app=ContentApp(module, lambda m: "hi"), tmp_path=tmp_path)
    msg = _query(rt, "schedule", "d_authored")
    assert [r["module_id"] for r in msg["schedule"]["provided_schedule"]] == ["AUDMED"]


# ---- the adaptive day plan + its "why" ----------------------------------------------
# The wire is unchanged; the served plan comes from a scored recommender and the
# reasoning is kept for the parent (factors in isolation: `test_schedule_planner.py`).

def test_schedule_query_stores_the_why_behind_the_day_it_served(tmp_path,
                                                                device_id="d_why"):
    rt = _activity_runtime(device_id, tmp_path)
    msg = _query(rt, "schedule", device_id)
    served = [r["module_id"] for r in msg["schedule"]["provided_schedule"]]
    stored = rt.store.read(device_id, "schedule_explain")
    assert stored, "the plan's explanations were not persisted"
    assert [e["module_id"] for e in stored["explanations"]] == served
    assert all(e["line"].endswith(".") for e in stored["explanations"])
    # the reasoning is stored, never served on the wire
    assert "explanations" not in json.dumps(msg["schedule"])


def test_schedule_view_returns_the_served_plan_its_why_and_its_inputs(tmp_path,
                                                                      device_id="d_view2"):
    rt = _activity_runtime(device_id, tmp_path)
    _activity(rt, {"subtopic": "query", "query": "schedule"}, device_id)
    view = rt.schedule_view(device_id)
    assert view["ok"] and view["served"] is True
    assert view["schedule"]["provided_schedule"]
    assert len(view["explanations"]) == len(view["schedule"]["provided_schedule"])
    assert view["inputs"]["telemetry"]["carries_module_signal"] is False
    assert view["inputs"]["bedtime"]["enabled"] is False


def test_schedule_view_plans_on_demand_for_a_robot_that_has_not_pulled_one(tmp_path):
    rt = _activity_runtime("d_ondemand", tmp_path)
    view = rt.schedule_view("d_ondemand")
    assert view["ok"] and view["served"] is False and view["explanations"]
    assert rt.schedule_view("d_never_seen")["ok"] is False


def test_schedule_view_plans_for_an_offline_robot_the_roster_knows(tmp_path):
    """After a restart a robot with no mentor behaviors is absent from `self.robots`, but
    the durable roster knows it — so the card plans its day, not "unknown device"."""
    rt = _activity_runtime("d_offline", tmp_path)
    rt.robots.pop("d_offline", None)
    assert rt._roster_seen("d_offline")
    view = rt.schedule_view("d_offline")
    assert view["ok"] and view["explanations"], view


def test_a_parent_requested_activity_lands_at_the_hour_they_asked_for(tmp_path):
    """`SchedulePreferences.parent_requests[]` pins a 16:00 request to the 16:00 slot. A
    FIXED day for both request and `now`, so a run crossing midnight cannot unpin it."""
    did = "d_pref"
    rt = _rt(did, nickname="Sam", tmp_path=tmp_path)
    day = datetime.date(2026, 9, 2)
    at_four = datetime.datetime.combine(day, datetime.time(16, 0))
    rt._config_overrides[did] = {"schedule_preferences": {"parent_requests": [
        {"module_id": "STORY", "scheduled_at": int(at_four.timestamp())}]}}
    sched, expl, _ = rt.plan_schedule_for(
        did, now=datetime.datetime.combine(day, datetime.time(15, 0)))
    pinned = [e for e in expl if "parent_request" in e["reason_codes"]]
    assert [e["module_id"] for e in pinned] == ["STORY"]
    assert pinned[0]["at"] == "16:00"
    assert pinned[0]["line"] == ("Requested by a parent for 4:00 pm — "
                                 "Story is pinned to that slot.")
    assert "STORY" in [r["module_id"] for r in sched["provided_schedule"]]


def test_status_server_serves_the_schedule_and_its_explanations(tmp_path):
    """`GET /schedule?device_id=…` — the parent-facing read of the planned day."""
    did = "d_httpsched"
    rt = _rt(did, nickname="Sam", tmp_path=tmp_path)
    _activity(rt, {"subtopic": "query", "query": "schedule"}, did)
    port = free_port()
    rt._start_status_server(port)
    view = _http_get(port, f"/schedule?device_id={did}")
    assert view["ok"] and view["device_id"] == did
    assert [e["module_id"] for e in view["explanations"]] == \
        [r["module_id"] for r in view["schedule"]["provided_schedule"]]
    assert all(e["line"] for e in view["explanations"])
    assert view["inputs"]["child_name"] == "Sam"
    _http_404(port, "/schedule?device_id=d_nope")
