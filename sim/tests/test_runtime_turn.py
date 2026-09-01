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


def _drive(app, device_id="d_test", speech="hello"):
    rt = moxie_runtime.MoxieRuntime(app=app, child=ChildProfile(nickname="Sam"))
    rt.client = _FakeClient()                             # inject fake transport
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
    """M5 integration: _push_config emits a spec-conformant RobotCloudConfig on /config."""
    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile(nickname="Sam"))
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
    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile(nickname="Sam"))
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
    rt = moxie_runtime.MoxieRuntime(app=_ActionApp(), child=ChildProfile())
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
