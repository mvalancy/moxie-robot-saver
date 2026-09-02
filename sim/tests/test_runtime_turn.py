"""
Integration test — a turn round-trips through the REAL MoxieRuntime pipeline with a
fake MQTT transport (no broker). Covers M1: that a brain's Reply (text + actions +
ResultCode) reaches the robot as a spec-conformant RemoteChatResponse on
/devices/{id}/commands/remote_chat. Exercises _on_remote_chat → _handle_turn →
_publish_chat → build_chat_response → client.publish — the actual runtime code.
"""
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))

from moxie_sdk.app import MoxieApp                       # noqa: E402
from moxie_sdk.types import Reply, Action, ActionType, RobotContext, ChildProfile  # noqa: E402
import moxie_runtime                                     # noqa: E402


class _FakeClient:
    """Records publishes; no network."""
    def __init__(self):
        self.published = []

    def publish(self, topic, payload):
        self.published.append((topic, json.loads(payload)))


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


def _drive(app, device_id="d_test", speech="hello", synth=None):
    rt = moxie_runtime.MoxieRuntime(app=app, child=ChildProfile(nickname="Sam"))
    rt.client = _FakeClient()                             # inject fake transport
    if synth is not None:
        rt.set_synthesizer(synth)                         # server-side voice for the SIM
    rt.robots[device_id] = RobotContext(device_id=device_id, child=rt.child)
    event = json.dumps({"command": "prompt", "backend": "router",
                        "event_id": "evt-9", "speech": speech})
    rt._on_remote_chat(device_id, rt.robots[device_id], event)
    rt._pool.shutdown(wait=True)                          # flush the turn off the pool
    return rt.client.published


def _chat(published, device_id="d_test"):
    topic = f"/devices/{device_id}/commands/remote_chat"
    msgs = [p for (t, p) in published if t == topic]
    assert msgs, f"no remote_chat published; got {published}"
    return msgs[-1]


def test_turn_roundtrips_text_actions_and_success():
    published = _drive(_ActionApp(), speech="let's draw")
    resp = _chat(published)
    assert resp["command"] == "remote_chat"
    assert resp["result"] == "SUCCESS"
    assert resp["output"]["text"] == "You said: let's draw"
    assert resp["output"]["markup"]                       # markup auto-generated
    ra = resp["response_actions"]
    assert len(ra) == 1 and ra[0]["action"] == "launch"
    assert ra[0]["module_id"] == "DRAW" and ra[0]["content_id"] == "default"


def test_turn_publishes_decodable_tts_when_synth_set(device_id="d_test"):
    """With a server voice installed, a turn also publishes a CloudTTSResponse to
    /commands/tts that the SIM can decode back to audio — the runtime→SIM voice contract."""
    from moxie_sdk.tts import PiperSynthesizer, decode_cloud_tts_response
    synth = PiperSynthesizer("x.onnx", voice_fn=lambda t: b"AUDIO", sample_rate=22050)
    published = _drive(_ActionApp(), speech="hello", synth=synth)
    tts = [p for (t, p) in published if t == f"/devices/{device_id}/commands/tts"]
    assert tts, f"no tts published; got topics {[t for (t, _) in published]}"
    spoken = decode_cloud_tts_response(tts[-1])
    assert spoken["audio"] == b"AUDIO" and spoken["sample_rate"] == 22050
    assert spoken["event_id"] == "evt-9"                  # carries the turn's event id


def test_turn_no_tts_without_synth():
    published = _drive(_ActionApp(), speech="hello")      # no synthesizer configured
    assert not [t for (t, _) in published if t.endswith("/commands/tts")]


def test_offline_brain_signals_error_offline_over_the_wire():
    published = _drive(_OfflineApp())
    resp = _chat(published)
    assert resp["result"] == "ERROR_OFFLINE"              # robot uses local fallback


def test_content_module_runs_through_the_runtime():
    """End-to-end: a shipped content module, driven by ContentApp inside the real
    runtime, produces the module's reply on the wire (event→runtime→ContentApp→publish)."""
    from moxie_sdk.content import load_modules, ContentApp
    starter = os.path.join(REPO, "mqtt", "content_modules", "starter.json")
    with open(starter) as fh:
        module = load_modules(json.load(fh))
    app = ContentApp(module, lambda messages: "Dinosaurs are amazing!")
    rt = moxie_runtime.MoxieRuntime(app=app, child=ChildProfile(nickname="Sam"))
    rt.client = _FakeClient()
    did = "d_content"
    rt.robots[did] = RobotContext(device_id=did, child=rt.child,
                                  module_id="FREE_CHAT", content_id="default")
    rt._on_remote_chat(did, rt.robots[did],
                       json.dumps({"command": "prompt", "event_id": "e",
                                   "speech": "tell me about dinosaurs"}))
    rt._pool.shutdown(wait=True)
    resp = _chat(rt.client.published, did)
    assert resp["result"] == "SUCCESS"
    assert resp["output"]["text"] == "Dinosaurs are amazing!"


def test_history_accumulates_across_the_pipeline():
    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile())
    rt.client = _FakeClient()
    did = "d_hist"
    rt.robots[did] = RobotContext(device_id=did, child=rt.child)
    rt._on_remote_chat(did, rt.robots[did],
                       json.dumps({"command": "prompt", "event_id": "e", "speech": "hi"}))
    rt._pool.shutdown(wait=True)
    h = rt.history.get(did, [])
    assert {"role": "user", "content": "hi"} in h
    assert any(m["role"] == "assistant" for m in h)


def test_stt_frames_through_runtime_publish_transcript():
    """AI seam §1 integration: VAD audio frames fed to the runtime accumulate and,
    on END_OF_SPEECH, publish a zmqSTTResponse with the transcript (fake transcriber)."""
    from moxie_sdk.stt import Transcriber

    class _Fake(Transcriber):
        def transcribe(self, pcm, sample_rate=16000):
            return f"heard {len(pcm)}b"

    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile())
    rt.client = _FakeClient()
    rt.set_transcriber(_Fake())
    did = "d_stt"
    assert rt.feed_stt(did, 1, b"aa", uuid="u1") is None        # START_OF_SPEECH
    assert rt.feed_stt(did, 2, b"bb") is None                    # SPEECH
    out = rt.feed_stt(did, 3, b"cc")                             # END_OF_SPEECH
    assert out == "heard 6b"
    topic = f"/devices/{did}/commands/zmq"
    msgs = [p for (t, p) in rt.client.published if t == topic]
    assert msgs and msgs[-1]["type"] == "FINAL"
    assert msgs[-1]["speech"] == "heard 6b" and msgs[-1]["uuid"] == "u1"


def test_handle_zmq_json_audio_frame_drives_stt():
    """events/zmq → handle_zmq JSON bridge → feed_stt → published transcript."""
    import base64
    from moxie_sdk.stt import Transcriber

    class _Fake(Transcriber):
        def transcribe(self, pcm, sample_rate=16000):
            return "hello moxie"

    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile())
    rt.client = _FakeClient()
    rt.set_transcriber(_Fake())
    did = "d_zmq"
    a = base64.b64encode(b"xy").decode()
    rt.handle_zmq(did, json.dumps({"vad": 1, "audio_content": a, "uuid": "u9"}))
    got = rt.handle_zmq(did, json.dumps({"vad": 3, "audio_content": a, "uuid": "u9"}))
    assert got == "hello moxie"
    msgs = [p for (t, p) in rt.client.published if t == f"/devices/{did}/commands/zmq"]
    assert msgs[-1]["speech"] == "hello moxie"


def test_no_transcriber_ignores_audio():
    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile())
    rt.client = _FakeClient()
    assert rt.feed_stt("d", 3, b"aa") is None            # no transcriber → no-op
    assert rt.client.published == []


def test_push_config_publishes_spec_robot_cloud_config():
    """M5 integration: _push_config emits a spec-conformant RobotCloudConfig on /config.

    `allow_unverified_bots=True` is the pre-gate policy: this asserts the shape a
    **permitted** robot gets. What a device that is *not* on the permit list receives
    (no `child_pii`, `pairing_status:"unpairing"`) is `test_device_permits.py`."""
    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile(nickname="Sam"),
                                    allow_unverified_bots=True)
    rt.client = _FakeClient()
    did = "d_cfg"
    rt._push_config(did)
    msgs = [p for (t, p) in rt.client.published if t == f"/devices/{did}/config"]
    assert msgs, "no config published"
    cfg = msgs[-1]
    assert cfg["pairing_status"] == "paired"                 # the wrapper the robot needs
    assert cfg["child_pii"]["nickname"] == "Sam"
    assert cfg["data_sharing"] == "NO_DATA"                  # LoggingPolicy default
    assert "timezone_id" in cfg and "audio_volume" in cfg and "moxie_mode" in cfg
    assert cfg["settings"]["props"]["stt"] == "4"            # stream audio to our STT


def test_state_ingest_stores_robot_status():
    """M5 integration: a /state RobotStatus updates firmware + is stored for the UI."""
    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile())
    rt.client = _FakeClient()
    did = "d_state"
    rt.robots[did] = RobotContext(device_id=did, child=rt.child)
    rt._on_state(did, json.dumps({"robot_firmware_version": "v24.10.803",
                                  "battery_level": 0.9, "wifi_ssid": "home"}))
    assert rt.robots[did].firmware == "v24.10.803"
    assert rt.robots[did].extra["status"]["battery_level"] == 0.9


def test_update_config_republishes_with_merged_overrides():
    """M6 parent-console: update_config edits the RobotCloudConfig and re-publishes;
    overrides merge + persist across pushes."""
    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile(nickname="Sam"),
                                    allow_unverified_bots=True)
    rt.client = _FakeClient()
    did = "d_upd"
    rt.update_config(did, audio_volume=0.9, timezone_id="America/New_York")
    cfg = [p for (t, p) in rt.client.published if t == f"/devices/{did}/config"][-1]
    assert cfg["audio_volume"] == 0.9 and cfg["timezone_id"] == "America/New_York"
    assert cfg["child_pii"]["nickname"] == "Sam"          # base config intact
    rt.update_config(did, screen_brightness=0.5)           # a second edit merges
    cfg2 = [p for (t, p) in rt.client.published if t == f"/devices/{did}/config"][-1]
    assert cfg2["audio_volume"] == 0.9 and cfg2["screen_brightness"] == 0.5


def test_update_config_bedtime_window():
    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile(),
                                    allow_unverified_bots=True)
    rt.client = _FakeClient()
    cfg = rt.update_config("d_bt", weekday_bedtime=("20:00", "07:00"))
    assert cfg["weekday_bedtime_enabled"] is True
    assert cfg["weekday_bedtime_starts_at"] == "20:00"


def test_status_snapshot_surfaces_robot_state():
    """M6: the console snapshot carries each robot's live state from /state."""
    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile(nickname="Sam"))
    rt.client = _FakeClient()
    did = "d_snap"
    rt.robots[did] = RobotContext(device_id=did, child=rt.child)
    rt._on_state(did, json.dumps({"robot_firmware_version": "v24.10.803",
                                  "battery_level": 0.77, "wifi_ssid": "home", "mode": "idle"}))
    rt.update_config(did, audio_volume=0.8)
    snap = rt.status_snapshot()
    assert snap["ok"] and snap["app"]
    r = [x for x in snap["robots"] if x["device_id"] == did][0]
    assert r["battery_level"] == 0.77 and r["wifi_ssid"] == "home" and r["mode"] == "idle"
    assert r["firmware"] == "v24.10.803"
    assert r["config_overrides"]["audio_volume"] == 0.8


def test_tts_synthesizes_and_publishes_on_a_turn():
    """M4 integration: with a synthesizer set, a turn also publishes a CloudTTSResponse
    (server voice for the SIM); the real robot self-synthesizes so it's opt-in."""
    from moxie_sdk.tts import Synthesizer

    class _FakeSynth(Synthesizer):
        sample_rate = 16000
        channels = 1

        def synthesize(self, text, voice=None):
            return b"PCM:" + text.encode()

    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile(nickname="Sam"))
    rt.client = _FakeClient()
    rt.set_synthesizer(_FakeSynth())
    did = "d_tts"
    rt.robots[did] = RobotContext(device_id=did, child=rt.child)
    rt._on_remote_chat(did, rt.robots[did],
                       json.dumps({"command": "prompt", "event_id": "e", "speech": "hi"}))
    rt._pool.shutdown(wait=True)
    import base64
    tts = [p for (t, p) in rt.client.published if t == f"/devices/{did}/commands/tts"]
    assert tts, "no CloudTTSResponse published"
    audio = base64.b64decode(tts[-1]["audio"]["buffer"])
    assert audio.startswith(b"PCM:")                     # synthesized from the reply text
    assert tts[-1]["audio"]["sample_rate"] == 16000


def test_no_synthesizer_no_tts_published():
    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile())
    rt.client = _FakeClient()
    did = "d_notts"
    rt.robots[did] = RobotContext(device_id=did, child=rt.child)
    rt._on_remote_chat(did, rt.robots[did],
                       json.dumps({"command": "prompt", "event_id": "e", "speech": "hi"}))
    rt._pool.shutdown(wait=True)
    assert not [t for (t, p) in rt.client.published if t.endswith("/commands/tts")]


def test_telemetry_ingest_stores_and_counts():
    """M5 telemetry integration: an incoming Packet is stored per-device + counted in
    the console status snapshot."""
    from moxie_sdk.telemetry import build_packet
    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile())
    rt.client = _FakeClient()
    did = "d_tel"
    rt.robots[did] = RobotContext(device_id=did, child=rt.child)
    pkt = build_packet("wake", b"x", moxie_id=did)
    rt._on_event(did, "telemetry", json.dumps(pkt))
    rt._on_event(did, "telemetry", json.dumps(build_packet("said", "hi", moxie_id=did)))
    assert len(rt.robots[did].extra["telemetry"]) == 2
    snap = [r for r in rt.status_snapshot()["robots"] if r["device_id"] == did][0]
    assert snap["telemetry_count"] == 2


def test_handle_zmq_real_protobuf_frame_drives_stt():
    """A real robot's protobuf zmqSTTRequest frame off events/zmq → feed_stt → transcript."""
    from moxie_sdk.stt import Transcriber

    def _varint(n):
        out = bytearray()
        while True:
            b = n & 0x7F; n >>= 7
            out.append(b | (0x80 if n else 0))
            if not n:
                return bytes(out)

    def _frame(vad, audio, uuid):
        body = bytes([0x10]) + _varint(vad)
        body += bytes([0x1A]) + _varint(len(audio)) + audio
        u = uuid.encode(); body += bytes([0x22]) + _varint(len(u)) + u
        return b"embodied.perception.audio.zmqSTTRequest:" + body

    class _Fake(Transcriber):
        def transcribe(self, pcm, sample_rate=16000):
            return f"pb {len(pcm)}b"

    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile())
    rt.client = _FakeClient()
    rt.set_transcriber(_Fake())
    did = "d_pb"
    rt.handle_zmq(did, _frame(1, b"aa", "u5"))         # START
    got = rt.handle_zmq(did, _frame(3, b"bb", "u5"))    # END → transcribe
    assert got == "pb 4b"
    msgs = [pl for (t, pl) in rt.client.published if t == f"/devices/{did}/commands/zmq"]
    assert msgs[-1]["speech"] == "pb 4b" and msgs[-1]["uuid"] == "u5"


def test_telemetry_view_summarizes_stored_packets():
    """M6 insights: the runtime's per-robot telemetry view (what GET /telemetry serves)
    rolls the ingested Packets up by event and returns them newest-first."""
    from moxie_sdk.telemetry import build_packet
    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile())
    rt.client = _FakeClient()
    did = "d_view"
    rt.robots[did] = RobotContext(device_id=did, child=rt.child)
    for name, ts in (("wake", 100), ("said", 200), ("wake", 300)):
        rt.ingest_telemetry(did, json.dumps(
            build_packet(name, b"", moxie_id=did, recorded_at=ts)))
    view = rt.telemetry_view(did)
    assert view["ok"] and view["device_id"] == did
    assert view["summary"]["count"] == 3
    assert view["summary"]["by_event"] == {"wake": 2, "said": 1}
    assert view["summary"]["last_seen"]["wake"] == 300
    assert [e["event_name"] for e in view["events"]] == ["wake", "said", "wake"]


def test_telemetry_view_honors_limit_and_unknown_device():
    from moxie_sdk.telemetry import build_packet
    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile())
    rt.client = _FakeClient()
    did = "d_lim"
    rt.robots[did] = RobotContext(device_id=did, child=rt.child)
    for i in range(4):
        rt.ingest_telemetry(did, json.dumps(build_packet(f"e{i}", b"", moxie_id=did)))
    assert len(rt.telemetry_view(did, limit=2)["events"]) == 2
    missing = rt.telemetry_view("d_nope")
    assert missing["ok"] is False and "unknown device_id" in missing["error"]


def test_status_server_serves_status_and_telemetry():
    """The localhost status server answers GET /status and GET /telemetry (404 for an
    unknown device) — the endpoints the parent console reads."""
    import socket
    import urllib.error
    import urllib.request
    from moxie_sdk.telemetry import build_packet

    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile())
    rt.client = _FakeClient()
    did = "d_http"
    rt.robots[did] = RobotContext(device_id=did, child=rt.child)
    rt.ingest_telemetry(did, json.dumps(build_packet("wake", b"", moxie_id=did,
                                                     recorded_at=42)))
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    rt._start_status_server(port)

    def _get(path):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return json.loads(r.read().decode())

    assert _get("/status")["ok"] is True
    view = _get(f"/telemetry?device_id={did}&limit=5")
    assert view["ok"] and view["summary"]["by_event"] == {"wake": 1}
    assert view["events"][0]["recorded_at"] == 42
    try:
        _get("/telemetry?device_id=d_missing")
        assert False, "unknown device should 404"
    except urllib.error.HTTPError as e:
        assert e.code == 404
        assert json.loads(e.read().decode())["ok"] is False


# ---- activity-log queries (client-service-activity-log → commands/query_result) ----

def _activity_runtime(device_id="d_test", tmp_path=None):
    """A runtime with a fake transport and (optionally) its own on-disk store."""
    from moxie_sdk.store import JsonStore
    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile(),
                                    store=JsonStore(str(tmp_path)) if tmp_path else None)
    rt.client = _FakeClient()
    rt.robots[device_id] = RobotContext(device_id=device_id, child=rt.child)
    return rt


def _activity(rt, payload, device_id="d_test"):
    """Push one client-service-activity-log event through the REAL event router."""
    rt._on_event(device_id, "client-service-activity-log", json.dumps(payload))
    return rt.client.published


def _drive_activity(payload, device_id="d_test"):
    return _activity(_activity_runtime(device_id), payload, device_id)


def test_schedule_query_echoes_request_id_and_keys_schedule(device_id="d_test"):
    pub = _drive_activity({"subtopic": "query", "query": "schedule",
                           "request_id": "req-abc"}, device_id)
    assert len(pub) == 1
    topic, msg = pub[0]
    assert topic == f"/devices/{device_id}/commands/query_result"
    assert msg["command"] == "query_result"
    assert msg["query"] == "schedule"
    assert msg["request_id"] == "req-abc"          # the robot correlates on this
    assert "result" not in msg                     # the old generic key is gone


def test_schedule_query_serves_a_real_nonempty_day_plan(device_id="d_plan"):
    """ADOPT #1: the robot will not enter a session without a schedule — so the payload
    has to be a well-formed, non-empty ContentSchedule, not `{}`."""
    from moxie_sdk.schedule import validate_schedule
    _, msg = _activity(_activity_runtime(device_id),
                       {"subtopic": "query", "query": "schedule", "request_id": "r"},
                       device_id)[0]
    sched = msg["schedule"]
    assert validate_schedule(sched) == []
    ids = [r["module_id"] for r in sched["provided_schedule"]]
    assert len(ids) >= 8 and "DM" in ids
    assert "generate" not in sched                 # authoring key never hits the wire


def test_mentor_behaviors_query_echoes_request_id_and_keys_the_list(device_id="d_test"):
    pub = _drive_activity({"subtopic": "query", "query": "mentor_behaviors",
                           "request_id": "req-mbh-1"}, device_id)
    topic, msg = pub[0]
    assert topic == f"/devices/{device_id}/commands/query_result"
    assert msg["query"] == "mentor_behaviors"
    assert msg["request_id"] == "req-mbh-1"
    assert msg["mentor_behaviors"] == []           # nothing reported yet
    assert "result" not in msg


def test_license_query_uses_license_values():
    _, msg = _drive_activity({"subtopic": "query", "query": "license",
                              "request_id": "req-lic"})[0]
    assert msg["request_id"] == "req-lic"
    assert msg["license_values"] == []


def test_query_without_subtopic_is_still_answered():
    # looser senders omit `subtopic`; a bare `query` must not go unanswered
    _, msg = _drive_activity({"query": "schedule", "request_id": "req-bare"})[0]
    assert msg["query"] == "schedule" and msg["request_id"] == "req-bare"


def test_non_query_activity_subtopics_publish_nothing():
    # a mentor_behavior *report* and telehealth state are not query_result traffic
    assert _drive_activity({"mentor_behavior": {"module_id": "DM"}}) == []
    assert _drive_activity({"subtopic": "telehealth",
                            "message": {"state": "idle"}}) == []


# ---- mentor behaviors: report → store → serve (ADOPT #2) ----

_REPORT = {"timestamp": 1725000000000, "software_version": "24.10.803",
           "module_name": "robotbrain",
           "mentor_behavior": {"module_id": "DM", "content_id": "mission_1",
                               "content_day": "3", "timestamp": 1725000000000,
                               "action": "COMPLETED", "instance_id": 41,
                               "ended_reason": "MOXIE_ENDED"}}


def test_mentor_behavior_report_is_stored_and_served_back(tmp_path, device_id="d_mbh"):
    """The full ADOPT #2 loop through the real handlers: the robot reports a finished
    activity on client-service-activity-log (ActivityUpdate.mentor_behavior, Cloud.proto
    :241), the runtime stores it, and the next `mentor_behaviors` query serves it."""
    rt = _activity_runtime(device_id, tmp_path)
    assert _activity(rt, _REPORT, device_id) == []          # a report answers nothing
    _, msg = _activity(rt, {"subtopic": "query", "query": "mentor_behaviors",
                            "request_id": "r2"}, device_id)[0]
    assert msg["mentor_behaviors"] == [_REPORT["mentor_behavior"]]
    # envelope fields (100/101) are report metadata, not history
    assert "software_version" not in msg["mentor_behaviors"][0]


def test_stored_behaviors_survive_a_supervisor_restart(tmp_path, device_id="d_restart"):
    """The point of the durable store: a new runtime over the same data dir still knows
    what the child did."""
    _activity(_activity_runtime(device_id, tmp_path), _REPORT, device_id)
    rt2 = _activity_runtime(device_id, tmp_path)            # "restart"
    assert rt2.mentor_behaviors(device_id) == [_REPORT["mentor_behavior"]]


def test_reported_behaviors_are_served_newest_first(tmp_path, device_id="d_order"):
    rt = _activity_runtime(device_id, tmp_path)
    for ts in (100, 300, 200):
        _activity(rt, {"mentor_behavior": {"module_id": f"M{ts}", "timestamp": ts,
                                           "action": "COMPLETED"}}, device_id)
    assert [r["module_id"] for r in rt.mentor_behaviors(device_id)] == \
        ["M300", "M200", "M100"]


def test_a_useless_report_is_ignored(tmp_path, device_id="d_junk"):
    rt = _activity_runtime(device_id, tmp_path)
    assert rt.ingest_mentor_behavior(device_id, {"mentor_behavior": {}}) is None
    assert rt.ingest_mentor_behavior(device_id, {"mentor_behavior": {"action": "QUIT"}}) \
        is None                                             # no module_id → unusable
    assert rt.mentor_behaviors(device_id) == []


def test_reported_behaviors_shape_the_next_days_schedule(tmp_path, device_id="d_ftue"):
    """The two features meet: once onboarding is reported complete the schedule stops
    serving it, so FTUE actually ends (audit §4.1 row 2)."""
    rt = _activity_runtime(device_id, tmp_path)
    for i in range(9):
        _activity(rt, {"mentor_behavior": {"module_id": "TNT", "content_id": f"c{i}",
                                           "timestamp": 1000 + i,
                                           "action": "COMPLETED"}}, device_id)
    for i in range(4):
        _activity(rt, {"mentor_behavior": {"module_id": "SYSTEMSCHECK",
                                           "content_id": f"c{i}", "timestamp": 2000 + i,
                                           "action": "COMPLETED"}}, device_id)
    rt.client.published.clear()
    _, msg = _activity(rt, {"subtopic": "query", "query": "schedule",
                            "request_id": "r"}, device_id)[0]
    ids = [r["module_id"] for r in msg["schedule"]["provided_schedule"]]
    assert not ({"TNT", "SYSTEMSCHECK", "WELCOME"} & set(ids)), ids
    assert ids, "the day must not be empty once onboarding is done"


def test_schedule_uses_the_running_content_modules_schedules_block(tmp_path):
    """A ContentApp's `schedules[]` is the authoring surface the served plan comes from."""
    from moxie_sdk.content import load_modules, ContentApp
    from moxie_sdk.store import JsonStore
    module = load_modules({"schedules": [
        {"name": "quiet", "schedule": {"provided_schedule": [{"module_id": "AUDMED"}]}}]})
    rt = moxie_runtime.MoxieRuntime(app=ContentApp(module, lambda m: "hi"),
                                    child=ChildProfile(), store=JsonStore(str(tmp_path)))
    rt.client = _FakeClient()
    did = "d_authored"
    rt.robots[did] = RobotContext(device_id=did, child=rt.child)
    _, msg = _activity(rt, {"subtopic": "query", "query": "schedule",
                            "request_id": "r"}, did)[0]
    assert [r["module_id"] for r in msg["schedule"]["provided_schedule"]] == ["AUDMED"]


# ---- the adaptive day plan + its "why" (audit §4.2 BEYOND #7) ----------------------
#
# The wire is unchanged (the query tests above still hold); what is new is that the
# served plan comes from a scored recommender and that the reasoning is kept for the
# parent. See `sim/tests/test_schedule_planner.py` for the factors in isolation.

def test_schedule_query_stores_the_why_behind_the_day_it_served(tmp_path,
                                                                device_id="d_why"):
    rt = _activity_runtime(device_id, tmp_path)
    _, msg = _activity(rt, {"subtopic": "query", "query": "schedule",
                            "request_id": "r"}, device_id)[0]
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


def test_a_parent_requested_activity_lands_at_the_hour_they_asked_for(tmp_path):
    """`SchedulePreferences.parent_requests[]` (RobotCloudConfig field 28) is honored by
    the planner, not just stored: a 16:00 request is pinned to the 16:00 slot."""
    import datetime
    from moxie_sdk.store import JsonStore
    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile(nickname="Sam"),
                                    store=JsonStore(str(tmp_path)))
    rt.client = _FakeClient()
    did = "d_pref"
    rt.robots[did] = RobotContext(device_id=did, child=rt.child)
    at_four = datetime.datetime.combine(datetime.date.today(), datetime.time(16, 0))
    rt._config_overrides[did] = {"schedule_preferences": {"parent_requests": [
        {"module_id": "STORY", "scheduled_at": int(at_four.timestamp())}]}}
    sched, expl, _ = rt.plan_schedule_for(
        did, now=datetime.datetime.combine(datetime.date.today(), datetime.time(15, 0)))
    pinned = [e for e in expl if "parent_request" in e["reason_codes"]]
    assert [e["module_id"] for e in pinned] == ["STORY"]
    assert pinned[0]["at"] == "16:00"
    assert pinned[0]["line"] == ("Requested by a parent for 4:00 pm — "
                                 "Story is pinned to that slot.")
    assert "STORY" in [r["module_id"] for r in sched["provided_schedule"]]


def test_status_server_serves_the_schedule_and_its_explanations(tmp_path):
    """`GET /schedule?device_id=…` — the parent-facing read of the planned day."""
    import socket
    import urllib.error
    import urllib.request
    from moxie_sdk.store import JsonStore

    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile(nickname="Sam"),
                                    store=JsonStore(str(tmp_path)))
    rt.client = _FakeClient()
    did = "d_httpsched"
    rt.robots[did] = RobotContext(device_id=did, child=rt.child)
    _activity(rt, {"subtopic": "query", "query": "schedule"}, did)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    rt._start_status_server(port)
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/schedule?device_id={did}",
                                timeout=5) as r:
        view = json.loads(r.read().decode())
    assert view["ok"] and view["device_id"] == did
    assert [e["module_id"] for e in view["explanations"]] == \
        [r["module_id"] for r in view["schedule"]["provided_schedule"]]
    assert all(e["line"] for e in view["explanations"])
    assert view["inputs"]["child_name"] == "Sam"
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/schedule?device_id=d_nope",
                               timeout=5)
        assert False, "unknown device should 404"
    except urllib.error.HTTPError as e:
        assert e.code == 404
