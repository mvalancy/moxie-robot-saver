"""The bench recorder (`moxie_sdk.wire_record`), its timeline and `--share` copy
(`sim/tools/wire_timeline.py`) and its replay (`sim/tools/wire_replay.py`).

What is proved, hermetically, over one synthetic bench session fed through a fake paho client
with fixed clocks (this file reads no clock):

* the recorder subscribes to its two filters and **never publishes**, in its source and over a
  whole session; it is a separate process the supervisor never imports;
* each message is written with its direction and decoded body; no raw audio without
  `--audio`, the audio with it; the broker host it connected to is never written or said;
* the file is created 0600, never overwritten, and its first line says what it holds;
* a full queue drops messages, says how many where the gap is, and the writer keeps going; the
  size cap and a failed write each stop the recording with one line;
* the `--share` copy of the session holds none of its d_ ids, addresses, ports, MAC, hostname,
  username, Wi-Fi name, or the child's words or name, and each robot keeps one placeholder on
  every line; identity that survived the scrub makes the tool refuse;
* the committed fixtures under `sim/tests/data/wire/` are exactly the --share output of this
  session and pass the same greps; the timeline of the fixture is the committed golden (the
  re-prompt gap, the notify count, the chunk sequence); the replay reproduces the fixture's
  reply shapes against a fresh runtime with a scripted brain, and reports a tampered one.

`test_sil_*` (SIL group, real mosquitto): the recorder hears the virtual robot's echo turn in
order. Regenerate the fixtures after a deliberate change with
`python3 sim/tests/test_wire_record.py --write-fixtures`.
"""
from __future__ import annotations

import ast
import base64
import errno
import json
import os
import re
import stat
import sys
import threading

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for _p in (os.path.join(REPO, "mqtt"), os.path.join(REPO, "mqtt", "supervisor"),
           os.path.join(REPO, "sim"), os.path.dirname(os.path.abspath(__file__)),
           os.path.join(REPO, "sim", "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import helpers_audio as A                                          # noqa: E402
import wire_replay as R                                            # noqa: E402
import wire_timeline as T                                          # noqa: E402
from moxie_sdk import stt                                          # noqa: E402
from moxie_sdk import wire_record as W                             # noqa: E402
from moxie_sdk.tts import build_cloud_tts_response                  # noqa: E402
from moxie_sdk.types import Action, ActionType, ResultCode          # noqa: E402
from moxie_sdk.wire import (build_activity_response, build_chat_response,  # noqa: E402
                            build_remote_modules)

SOURCE = os.path.join(REPO, "mqtt", "moxie_sdk", "wire_record.py")
FIXTURE_DIR = os.path.join(REPO, "sim", "tests", "data", "wire")
FIXTURE = os.path.join(FIXTURE_DIR, "bench-session.jsonl")
GOLDEN = os.path.join(FIXTURE_DIR, "bench-session.timeline.txt")

# --------------------------------------------------------------------------- #
# The session's identity: every value made up, every address from a documentation range
# --------------------------------------------------------------------------- #
ROBOT_A = "d_5f2b8c1e-7a4d-4c3b-9e6f-0a1b2c3d4e5f"
ROBOT_B = "d_9c8b7a6f-5e4d-4321-8fed-cba987654321"
ADDRESS = "198.51.100.23"            # RFC 5737 TEST-NET-2
PORT = "51234"
IPV6 = "2001:db8::5e:10"             # RFC 3849
MAC = "00:00:5e:00:53:2a"            # RFC 7042
HOSTNAME = "moxie-bench.local"
USERNAME = "moxie-robot-user"
WIFI_NAME = "Robins House WiFi"
CHILD_NAME = "Robin"
CHILD_WORDS = "my turtle Sprout ate a strawberry"
MOXIE_WORDS = ("Robin, Sprout has excellent taste.", "Turtles love strawberries.",
               "What else does Sprout like to eat?")
GOODBYE = "bye moxie see you tomorrow"
BROKER_HOST = "wire-broker.invalid"  # where the recorder connects: never in the file
SECRET = "not-a-real-password"

E1 = "7f3a1c2e-0b4d-4e5f-8a9b-0c1d2e3f4a5b"     # unanswered: the robot asks again
E2 = "9b2d4e6f-1a3c-4b5d-8e7f-a0b1c2d3e4f5"     # the re-prompt, answered in three chunks
E3 = "3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e6f"     # a goodbye: exit_module
E4 = "4e5f6a7b-8c9d-4e0f-9a1b-2c3d4e5f6a7b"     # eb-found-face in the next module
E5 = "5a6b7c8d-9e0f-4a1b-8c2d-3e4f5a6b7c8d"     # the module list
NOTIFY_IDS = ("6b7c8d9e-0f1a-4b2c-9d3e-4f5a6b7c8d9e", "7c8d9e0f-1a2b-4c3d-8e4f-5a6b7c8d9e0f",
              "8d9e0f1a-2b3c-4d4e-9f5a-6b7c8d9e0f1a")
UTT_1 = "2f1c9a7e-5b3d-4c8e-9f0a-1b2c3d4e5f60"
UTT_2 = "3a2b1c0d-6e5f-4a7b-8c9d-0e1f2a3b4c5d"
END_OF_SESSION = 57.0

#: The child's words and name, the robot's identity: what a --share copy must not hold.
FORBIDDEN = (ROBOT_A, ROBOT_B, ROBOT_A[2:], ADDRESS, f":{PORT}", PORT, IPV6, MAC, HOSTNAME,
             USERNAME, WIFI_NAME, CHILD_NAME, CHILD_WORDS, "Sprout", "strawberr", GOODBYE,
             *MOXIE_WORDS, BROKER_HOST, SECRET)
GENERIC = {
    "a d_ followed by a hex uuid": re.compile(r"d_[0-9a-f]{8}-[0-9a-f]{4}-", re.I),
    "an IPv4 literal": re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])"),
    "an IPv6 literal": re.compile(r"[0-9a-f]{1,4}(?::[0-9a-f]{0,4}){2,7}", re.I),
    "a MAC": re.compile(r"(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}", re.I),
    "a :port after an address": re.compile(r"(?:\d|\])\s*:\d{2,5}\b"),
    "a .local/.lan/.home hostname": re.compile(r"\.(?:local|lan|home)\b", re.I),
    "a username slot": re.compile(r"\bu'"),
}


def _sys(t, level, line):
    return (t, ("msg", f"$SYS/broker/log/{level}", line.encode()))


def _dev(t, device, rest, payload):
    if not isinstance(payload, bytes):
        payload = json.dumps(payload).encode()
    return (t, ("msg", f"/devices/{device}/{rest}", payload))


def _prompt(event_id, speech, command="prompt", **extra):
    body = {"event_id": event_id, "command": command, "backend": "router", "speech": speech}
    body.update(extra)
    return body


def bench_session() -> list:
    """One bench session, as `(mono seconds, step)` in arrival order. A step is a broker
    message `("msg", topic, payload)` or the recorder's own `("connect",)`, `("suback",)`,
    `("lost",)`. It covers each section of the timeline once."""
    a, b, s = ROBOT_A, ROBOT_B, []
    s += [(0.05, ("connect",)), (0.06, ("suback",))]
    s.append(_sys(0.40, "N", f"New connection from {ADDRESS}:{PORT} on port 8883."))
    s.append(_sys(0.41, "N", f"New client connected from {ADDRESS}:{PORT} as {a} "
                             f"(p2, c1, k30, u'{USERNAME}')."))
    s.append(_dev(0.61, a, "state", {
        "software_version": "24.10.803", "state": "config", "battery_level": 0.82,
        "mac": MAC, "wifi_ssid": WIFI_NAME, "embodied_robot_id": a[2:],
        "timestamp": 1760000000610}))
    s.append(_dev(1.62, a, "config", {
        "pairing_status": "paired", "audio_volume": "0.6",
        "timezone_id": "America/Los_Angeles",
        "child_pii": {"nickname": CHILD_NAME, "input_speed": 0.0},
        "settings": {"props": {"stt": "4", "local_stt": "on"}}}))
    s.append(_dev(1.63, a, "commands/zmq", stt.encode_proto_subscribe(
        [stt.ZMQ_STT_REQUEST], timestamp_ms=1760000001630)))
    s.append(_dev(2.43, a, "state", {"software_version": "24.10.803", "state": "config"}))
    # The child speaks (1.1 s at speech level) and is heard.
    frames = A.stt_frames(A.tone_pcm(1100, rms=0.03), uuid=UTT_1, frame_ms=200)
    s += [_dev(2.48 + 0.2 * i, a, "events/zmq", f) for i, f in enumerate(frames)]
    end_1 = 2.48 + 0.2 * (len(frames) - 1)
    s.append(_dev(round(end_1 + 0.62, 2), a, "commands/zmq", stt.encode_zmq_stt_response(
        UTT_1, CHILD_WORDS, timestamp_ms=1760000004500)))
    s.append(_dev(4.40, a, "events/remote-chat",
                  _prompt(E1, CHILD_WORDS, module_id="FREE_CHAT", content_id="default")))
    # Room tone, short and quiet: the ears hear nothing.
    quiet = A.stt_frames(A.tone_pcm(240, rms=0.004), uuid=UTT_2, frame_ms=200)
    s += [_dev(9.00 + 0.2 * i, a, "events/zmq", f) for i, f in enumerate(quiet)]
    s.append(_dev(9.55, a, "commands/zmq", stt.encode_zmq_stt_response(
        UTT_2, "", timestamp_ms=1760000009550)))
    # The cloud stayed silent on E1; the robot asks again 20.20 s later.
    s.append(_dev(24.60, a, "events/remote-chat",
                  _prompt(E2, CHILD_WORDS, "reprompt", module_id="FREE_CHAT",
                          content_id="default")))
    tone = A.tone_pcm(400, rms=0.2, sample_rate=22050)
    for i, (t, words) in enumerate(zip((25.70, 26.30, 27.00), MOXIE_WORDS)):
        last = i == len(MOXIE_WORDS) - 1
        s.append(_dev(t, a, "commands/remote_chat", build_chat_response(
            E2, words, result=ResultCode.SUCCESS if last else ResultCode.REPLY_PENDING,
            chunk_num=i, is_completed=last)))
        s.append(_dev(round(t + 0.01, 2), a, "commands/tts", build_cloud_tts_response(
            tone, event_id=E2, sample_rate=22050, chunk_num=i)))
    for i, (t, words) in enumerate(zip((27.40, 27.90, 28.40), MOXIE_WORDS)):
        notify = {"event_id": NOTIFY_IDS[i], "command": "notify", "backend": "router",
                  "speech": words, "module_id": "FREE_CHAT", "content_id": "default"}
        if i == 0:
            notify["extra_lines"] = [{"context_type": "input", "text": CHILD_WORDS}]
        s.append(_dev(t, a, "events/remote-chat", notify))
    s.append(_dev(30.00, a, "events/client-service-activity-log", {
        "timestamp": 1760000030000, "subtopic": "query", "query": "schedule",
        "request_id": "q-0001", "auid": a, "software_version": "24.10.803"}))
    s.append(_dev(30.05, a, "commands/query_result",
                  build_activity_response("schedule", request_id="q-0001")))
    s.append(_dev(31.00, a, "events/remote-chat",
                  _prompt(E3, GOODBYE, module_id="FREE_CHAT", content_id="default")))
    s.append(_dev(31.90, a, "commands/remote_chat", build_chat_response(
        E3, f"Bye {CHILD_NAME}! See you tomorrow.", actions=[Action(type=ActionType.EXIT)])))
    s.append(_dev(33.90, a, "events/remote-chat",
                  _prompt(E4, "eb-found-face", module_id="HUB", content_id="default")))
    s.append(_dev(33.93, a, "commands/remote_chat",
                  build_chat_response(E4, "", result=ResultCode.NOREPLY_ACK)))
    s.append(_dev(34.50, a, "events/remote-chat", {
        "event_id": E5, "backend": "data", "query": {"query": "modules"}, "module_id": "HUB"}))
    s.append(_dev(34.55, a, "commands/remote_chat", build_chat_response(
        E5, "", backend="data",
        query_data=build_remote_modules([("FREE_CHAT", ["default"])]))))
    s.append(_dev(35.00, a, "events/telemetry", {
        "event_name": "SESSION_START", "timestamp": 1760000035000}))
    s.append(_dev(35.50, a, "events/device-logs", {
        "tag": "WifiApp",
        "message": f"joined {WIFI_NAME} as {HOSTNAME} ({ADDRESS}, {MAC}) for {CHILD_NAME}"}))
    # A second robot, over IPv6.
    s.append(_sys(40.00, "N", f"New client connected from {IPV6}:{PORT} as {b} "
                              f"(p2, c1, k60, u'{USERNAME}')."))
    s.append(_dev(40.30, b, "state", {"software_version": "24.10.801", "state": "config"}))
    s.append(_dev(41.31, b, "config", {"pairing_status": "paired"}))
    # The recorder itself loses the broker for two seconds.
    s += [(44.00, ("lost",)), (46.10, ("connect",)), (46.11, ("suback",))]
    s.append(_sys(50.00, "N", f"Client {b} closed its connection."))
    # The first robot comes back by name, then its old session and its socket go.
    s.append(_sys(52.00, "N", f"New client connected from {HOSTNAME}:{PORT} as {a} "
                              f"(p2, c1, k30, u'{USERNAME}')."))
    s.append(_sys(52.10, "E", f"Client {a} already connected, closing old connection."))
    s.append(_sys(53.00, "N", f"Socket error on client {a} [{IPV6}]:{PORT} ({MAC}), "
                              f"disconnecting."))
    s.append(_sys(55.00, "N", f"Client {a} closed its connection."))
    s.append(_sys(56.00, "E", "OpenSSL Error[0]: error:0A000418:SSL routines::tlsv1 alert "
                              "unknown ca"))
    return s


# --------------------------------------------------------------------------- #
# Driving the recorder through a fake paho client, on a scripted clock
# --------------------------------------------------------------------------- #
class FakePaho:
    """paho's client, as much as the recorder uses, recording every call that matters."""

    def __init__(self, client_id):
        self.client_id = client_id
        self.published: list = []
        self.subscribed: list = []
        self.subscribe_calls = 0
        self.connected_to = None
        self.credentials = None
        self.looping = False

    def username_pw_set(self, username, password=None):
        self.credentials = (username, password)

    def reconnect_delay_set(self, min_delay=1, max_delay=120):
        pass

    def connect_async(self, host, port=1883, keepalive=60):
        self.connected_to = (host, port, keepalive)

    def loop_start(self):
        self.looping = True

    def loop_stop(self):
        self.looping = False

    def disconnect(self):
        pass

    def subscribe(self, topic, qos=0):
        self.subscribe_calls += 1
        self.subscribed.extend([topic] if isinstance(topic, str) else [t[0] for t in topic])
        return (0, self.subscribe_calls)

    def publish(self, *args, **kwargs):          # the recorder must never get here
        self.published.append((args, kwargs))


class _Msg:
    def __init__(self, topic, payload):
        self.topic, self.payload = topic, payload


class _Clock:
    """The recorder's two clocks, moved by the test: `wall` is an epoch, `mono` seconds."""

    def __init__(self):
        self.now = 0.0

    def wall(self):
        return 1760000000.0 + self.now

    def mono(self):
        return self.now


def record(session, out, *, keep_audio=False, max_bytes=W.DEFAULT_MAX_MB * 2 ** 20,
           threaded=True, fh=None):
    """Feed `session` through a recorder on a fake client; returns `(recorder, client,
    said)` after `stop()`."""
    clock, said = _Clock(), []
    rec = W.WireRecorder(str(out), keep_audio=keep_audio, max_bytes=max_bytes,
                         wall=clock.wall, mono=clock.mono, say=said.append, fh=fh)
    rec.open()
    if threaded:
        rec.start()
    client = rec.connect(BROKER_HOST, 1883, "supervisor", SECRET, client_factory=FakePaho)
    for t, step in session:
        clock.now = t
        if step[0] == "connect":
            rec.on_connect(client, None, {}, 0, None)
        elif step[0] == "suback":
            rec.on_subscribe(client, None, client.subscribe_calls, [0, 0], None)
        elif step[0] == "lost":
            rec.on_disconnect(client, None, {}, None, None)
        else:
            rec.on_message(client, None, _Msg(step[1], step[2]))
    clock.now = END_OF_SESSION
    rec.close_client()
    rec.stop("stopped by hand (Ctrl-C)")
    return rec, client, said


def lines_of(path) -> list:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    out = tmp_path_factory.mktemp("wire") / "bench.jsonl"
    rec, client, said = record(bench_session(), out)
    return {"path": str(out), "rec": rec, "client": client, "said": said,
            "lines": lines_of(out), "text": open(out, encoding="utf-8").read()}


def _msgs(lines, device=None, name=None, direction=None):
    return [ln for ln in lines if ln.get("kind") == "msg"
            and (device is None or ln["device"] == device)
            and (name is None or ln["name"] == name)
            and (direction is None or ln["dir"] == direction)]


# --------------------------------------------------------------------------- #
# 1. Read-only: two filters, never a publish
# --------------------------------------------------------------------------- #
def test_the_recorder_source_never_calls_publish():
    """No call to anything named `publish` anywhere in the module, and no paho publish
    helper imported: the pin that bites when one is added."""
    tree = ast.parse(open(SOURCE, encoding="utf-8").read())
    calls = sorted({node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
                    for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, (ast.Attribute, ast.Name))
                    and "publish" in (node.func.attr if isinstance(node.func, ast.Attribute)
                                      else node.func.id).lower()})
    assert calls == [], f"the recorder calls {calls}: it must never publish"
    imported = [alias.name for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names
                if "publish" in alias.name or "publish" in (getattr(node, "module", "") or "")]
    assert imported == [], imported


def test_a_whole_session_publishes_nothing_and_subscribes_to_the_two_filters(recorded):
    client = recorded["client"]
    assert client.published == [], client.published
    # Two connects (the second after the recorder lost the broker), one call each.
    assert client.subscribe_calls == 2
    assert client.subscribed == list(W.FILTERS) * 2 == \
        ["/devices/#", "$SYS/broker/log/#", "/devices/#", "$SYS/broker/log/#"]
    assert client.client_id.startswith("wire-recorder-")
    assert not client.client_id.startswith("d_"), "the supervisor would take it for a robot"
    assert client.credentials == ("supervisor", SECRET)
    assert client.connected_to[:2] == (BROKER_HOST, 1883)


def test_the_supervisor_never_imports_the_recorder():
    """A separate process: no runtime module imports it, so no turn depends on it."""
    import helpers_runtime as H
    hits = [path for path, src in H.runtime_sources().items() if "wire_record" in src]
    assert hits == [], hits


# --------------------------------------------------------------------------- #
# 2. What each line holds
# --------------------------------------------------------------------------- #
def test_each_message_gets_its_direction_and_its_decoded_body(recorded):
    lines = recorded["lines"]
    first = lines[0]
    assert first["kind"] == "banner" and first["format"] == W.FORMAT
    assert first["filters"] == list(W.FILTERS) and first["audio"] is False

    state = _msgs(lines, ROBOT_A, "state")[0]
    assert (state["dir"], state["as"], state["mono"]) == ("robot>cloud", "json", 0.61)
    assert state["body"]["wifi_ssid"] == WIFI_NAME          # kept as sent: the raw is private
    assert state["t"] == 1760000000.61 and state["bytes"] > 0

    ask = _msgs(lines, ROBOT_A, "zmq", "cloud>robot")[0]
    assert ask["body"] == {"proto": stt.PROTO_SUBSCRIBE, "protos": [stt.ZMQ_STT_REQUEST]}

    frames = [m for m in _msgs(lines, ROBOT_A, "zmq", "robot>cloud")
              if m["body"].get("uuid") == UTT_1]
    assert [f["body"]["vad_name"] for f in frames] == \
        ["START_OF_SPEECH"] + ["SPEECH"] * 5 + ["END_OF_SPEECH"]
    pcm = A.tone_pcm(1100, rms=0.03)
    first_chunk = pcm[:6400]                                 # 200 ms at 16 kHz PCM16
    _, rms = stt.audio_stats(first_chunk, 16000)
    assert frames[0]["body"]["audio"] == {"bytes": 6400, "rms": round(rms, 5)}
    assert sum(f["body"]["audio"]["bytes"] for f in frames) == len(pcm)

    heard = [m for m in _msgs(lines, ROBOT_A, "zmq", "cloud>robot")
             if m["body"]["proto"] == stt.ZMQ_STT_RESPONSE]
    assert heard[0]["body"]["speech"] == CHILD_WORDS and heard[0]["body"]["type"] == "FINAL"
    assert heard[1]["body"]["speech"] == "" and heard[1]["body"]["uuid"] == UTT_2

    asked = _msgs(lines, ROBOT_A, "remote-chat")
    assert asked[0]["body"] == _prompt(E1, CHILD_WORDS, module_id="FREE_CHAT",
                                       content_id="default")
    replies = _msgs(lines, ROBOT_A, "remote_chat")
    assert all(r["dir"] == "cloud>robot" and r["as"] == "json" for r in replies)
    assert replies[0]["body"] == build_chat_response(
        E2, MOXIE_WORDS[0], result=ResultCode.REPLY_PENDING, chunk_num=0, is_completed=False)

    voice = _msgs(lines, ROBOT_A, "tts")[0]
    assert voice["dir"] == "cloud>robot"
    tone = A.tone_pcm(400, rms=0.2, sample_rate=22050)
    assert voice["body"]["audio"] == {"bytes": len(tone), "sample_rate": 22050, "channels": 1}
    assert voice["body"]["event_id"] == E2 and voice["body"]["chunk_num"] == 0

    log = [m for m in _msgs(lines, direction="broker")]
    assert all(m["name"] in ("log/N", "log/E") and m["as"] == "log" for m in log)
    connect = log[1]
    assert connect["device"] == ROBOT_A and USERNAME in connect["body"]["text"]
    assert lines[-1]["kind"] == "stop" and lines[-1]["reason"] == "stopped by hand (Ctrl-C)"


def test_no_raw_audio_without_the_audio_switch_and_the_audio_with_it(recorded, tmp_path):
    pcm = A.tone_pcm(1100, rms=0.03)
    tone = A.tone_pcm(400, rms=0.2, sample_rate=22050)
    tts_b64 = base64.b64encode(tone).decode()
    frame_b64 = base64.b64encode(pcm[:6400]).decode()
    assert tts_b64 not in recorded["text"] and frame_b64 not in recorded["text"]
    assert '"buffer"' not in recorded["text"] and "audio_b64" not in recorded["text"]

    _, _, said = record(bench_session(), tmp_path / "with-audio.jsonl", keep_audio=True)
    text = open(tmp_path / "with-audio.jsonl", encoding="utf-8").read()
    assert tts_b64 in text and frame_b64 in text
    lines = lines_of(tmp_path / "with-audio.jsonl")
    assert lines[0]["audio"] is True
    frame = next(m for m in _msgs(lines, ROBOT_A, "zmq", "robot>cloud")
                 if m["body"].get("uuid") == UTT_1)
    assert base64.b64decode(frame["body"]["audio_b64"]) == pcm[:6400]
    assert any("--audio" in line for line in said)


def test_the_broker_host_is_never_written_or_said(recorded):
    assert BROKER_HOST not in recorded["text"]
    assert SECRET not in recorded["text"]
    assert not any(BROKER_HOST in line or SECRET in line for line in recorded["said"])


def test_the_banner_says_what_the_file_holds_and_the_file_is_private(recorded, tmp_path):
    first = recorded["lines"][0]
    for words in ("what the child says", "the child's name", "address", "delete it",
                  "--share"):
        assert words in first["note"], words
    assert any(W.BANNER in line for line in recorded["said"])
    assert stat.S_IMODE(os.stat(recorded["path"]).st_mode) == 0o600
    with pytest.raises(FileExistsError):
        W.WireRecorder(recorded["path"], say=lambda line: None).open()
    notes = [ln["text"] for ln in recorded["lines"] if ln["kind"] == "note"]
    assert notes == ["connected to the broker",
                     "subscribed to /devices/# and $SYS/broker/log/#",
                     "lost the broker (connection lost); reconnecting",
                     "reconnected to the broker",
                     "subscribed to /devices/# and $SYS/broker/log/#"]


# --------------------------------------------------------------------------- #
# 3. Bounded: a full queue, the size cap, a failed write
# --------------------------------------------------------------------------- #
def _feed(rec, n, start=0):
    for i in range(start, start + n):
        rec.on_message(None, None, _Msg(f"/devices/{ROBOT_A}/events/telemetry",
                                        json.dumps({"n": i}).encode()))


def test_a_full_queue_drops_says_how_many_where_and_the_writer_keeps_going(tmp_path):
    clock, said = _Clock(), []
    rec = W.WireRecorder(str(tmp_path / "q.jsonl"), queue_max=3, wall=clock.wall,
                         mono=clock.mono, say=said.append)
    rec.open()
    _feed(rec, 5)                       # three fit; the writer is not running: two dropped
    rec.drain()                         # the writer catches up...
    _feed(rec, 1, start=5)              # ...and the next message goes in after the count
    rec.stop("done")
    lines = lines_of(tmp_path / "q.jsonl")
    kinds = [ln["kind"] for ln in lines]
    assert kinds == ["banner", "msg", "msg", "msg", "dropped", "msg", "stop"], kinds
    assert [ln["body"]["n"] for ln in lines if ln["kind"] == "msg"] == [0, 1, 2, 5]
    assert lines[4]["count"] == 2 and lines[-1]["dropped"] == 2
    assert sum("dropping messages" in line for line in said) == 1


def test_the_size_cap_stops_the_recording_with_one_line(tmp_path):
    clock, said = _Clock(), []
    cap = 4096
    rec = W.WireRecorder(str(tmp_path / "cap.jsonl"), max_bytes=cap, wall=clock.wall,
                         mono=clock.mono, say=said.append)
    rec.open()
    _feed(rec, 200)
    rec.stop("done")
    lines = lines_of(tmp_path / "cap.jsonl")
    assert os.path.getsize(tmp_path / "cap.jsonl") <= cap
    stops = [ln for ln in lines if ln["kind"] == "stop"]
    assert len(stops) == 1 and lines[-1] is stops[0]
    assert stops[0]["reason"] == f"size cap reached ({cap} bytes)"
    assert 0 < sum(ln["kind"] == "msg" for ln in lines) < 200
    assert sum("stopped" in line for line in said) == 1
    assert rec.stopped.is_set()
    _feed(rec, 1)                                   # ignored after the cap
    assert rec.lines == sum(ln["kind"] == "msg" for ln in lines)


class _FullDisk:
    """A file that takes `room` writes, then fails as a full disk does."""

    def __init__(self, room):
        self.room, self.text = room, []

    def write(self, line):
        if self.room <= 0:
            raise OSError(errno.ENOSPC, "No space left on device")
        self.room -= 1
        self.text.append(line)

    def flush(self):
        pass

    def close(self):
        pass


def test_a_failed_write_stops_the_recording_with_one_line():
    clock, said, disk = _Clock(), [], _FullDisk(room=4)
    rec = W.WireRecorder(wall=clock.wall, mono=clock.mono, say=said.append, fh=disk)
    rec.open()
    rec.start()
    _feed(rec, 20)
    assert rec.stop("done").startswith("could not write the recording")
    assert len(disk.text) == 4                      # the banner and three messages
    assert sum("could not write" in line for line in said) == 1
    assert sum("stopped" in line for line in said) == 1


# --------------------------------------------------------------------------- #
# 4. The --share copy
# --------------------------------------------------------------------------- #
def _leaks(text: str) -> list:
    found = [repr(v) for v in FORBIDDEN if v in text]
    found += [kind for kind, rx in GENERIC.items() if rx.search(text)]
    return found


def test_the_share_copy_holds_no_identity_and_no_words(recorded, tmp_path):
    raw = recorded["text"]
    assert all(v in raw for v in (ROBOT_A, ROBOT_B, ADDRESS, IPV6, MAC, HOSTNAME, USERNAME,
                                  WIFI_NAME, CHILD_NAME, CHILD_WORDS)), \
        "the raw recording must hold every value, or the greps below prove nothing"
    out = tmp_path / "bench.share.jsonl"
    assert T.main([recorded["path"], "--share", str(out)]) == 0
    shared = open(out, encoding="utf-8").read()
    assert _leaks(shared) == []
    assert T.share_problems(T.load(str(out))) == []


def test_each_robot_keeps_one_placeholder_on_every_line(recorded):
    raw = [ln for ln in recorded["lines"] if ln["kind"] != "banner"]
    shared = T.share_records(recorded["lines"])[1:]
    assert len(raw) == len(shared)
    for before, after in zip(raw, shared):
        text_before, text_after = json.dumps(before), json.dumps(after)
        for real, placeholder in ((ROBOT_A, "d_robot-1"), (ROBOT_B, "d_robot-2")):
            if real in text_before:
                assert placeholder in text_after, (before, after)
            else:
                assert placeholder not in text_after, (before, after)
    connects = [ln["body"]["text"] for ln in shared
                if ln.get("dir") == "broker" and "connected from" in ln["body"]["text"]]
    assert connects == [
        "New client connected from [address] as d_robot-1 (p2, c1, k30).",
        "New client connected from [address] as d_robot-2 (p2, c1, k60).",
        "New client connected from [address] as d_robot-1 (p2, c1, k30)."]


#: Broker lines in shapes the session does not use: a username outside a connect line, an
#: IPv4-mapped IPv6 peer, a host with its port, a zoned IPv6, both MAC spellings, a public
#: hostname, and a connect line whose username holds a comma.
ODD_LOG_LINES = (
    f"Client {ROBOT_A} (u'{USERNAME}') was refused.",
    f"New connection from ::ffff:{ADDRESS}:{PORT} on port 1883.",
    f"Error resolving {HOSTNAME}:{PORT}.",
    f"Peer [fe80::1%eth0]:{PORT} closed.",
    "Device 00-00-5e-00-53-2a seen.",
    "Device 0000.5e00.532a seen.",
    "Bridge to bench-gateway.example.org failed.",
    f"New client connected from {ADDRESS}:{PORT} as weird-client (p2, c0, k15, u'x, p9').",
)


def test_the_scrub_handles_identity_in_any_broker_line_and_any_body():
    records = [{"kind": "msg", "mono": float(i), "dir": "broker", "device": "",
                "topic": "$SYS/broker/log/N", "name": "log/N", "bytes": len(line),
                "as": "log", "body": {"text": line}} for i, line in enumerate(ODD_LOG_LINES)]
    body = {"event_id": HOSTNAME, "module_id": ADDRESS, "command": "prompt",
            "speech": "6", "name": CHILD_NAME, ROBOT_A: 1,
            "input_vars": {"$eb_qr_value": f"WIFI:S:{WIFI_NAME};P:{SECRET};;"},
            "extra_lines": [{"context_type": "input", "text": "yes"}]}
    records.append({"kind": "msg", "mono": 9.0, "dir": "robot>cloud", "device": ROBOT_A,
                    "topic": f"/devices/{ROBOT_A}/events/remote-chat", "name": "remote-chat",
                    "bytes": 1, "as": "json", "body": body})
    shared = T.share_records(records)
    text = "".join(T.dump(r) for r in shared)
    for value in (ADDRESS, PORT, USERNAME, HOSTNAME, "fe80", "eth0", "00-00-5e-00-53-2a",
                  "0000.5e00.532a", "bench-gateway", CHILD_NAME, WIFI_NAME, SECRET, ROBOT_A,
                  "p9"):
        assert value not in text, value
    assert T.share_problems(shared) == []
    out = shared[-1]["body"]
    assert (out["speech"], out["extra_lines"][0]["text"]) == ("[text]", "[text]"), \
        "a child's one-word answer is still the child's words"
    assert (out["event_id"], out["module_id"], out["command"]) == ("[text]", "[text]", "prompt")
    assert out["d_robot-1"] == 1 and out["input_vars"] == {"$eb_qr_value": "[text]"}
    assert shared[-1]["body"]["name"] == "[text]"
    assert shared[-2]["body"]["text"] == \
        "New client connected from [address] as weird-client (p2, c0, k15)."


def test_the_share_copy_is_its_own_share_copy(recorded):
    once = T.share_records(recorded["lines"])
    assert T.share_records(once) == once


def test_identity_left_after_the_scrub_makes_the_tool_refuse(recorded, tmp_path,
                                                             monkeypatch, capsys):
    monkeypatch.setattr(T.Share, "log_line", lambda self, text: text)   # scrub nothing
    out = tmp_path / "refused.jsonl"
    assert T.main([recorded["path"], "--share", str(out)]) == 1
    assert not out.exists(), "a refused copy must not be written at all"
    err = capsys.readouterr().err
    assert "refused" in err and "a username" in err
    assert ADDRESS not in err and USERNAME not in err, "the refusal names kinds, not values"


# --------------------------------------------------------------------------- #
# 5. The committed fixtures, the timeline golden, the replay
# --------------------------------------------------------------------------- #
def regenerate(tmp_dir) -> tuple:
    """`(share text, timeline text)` the tools produce for `bench_session()` today."""
    raw = os.path.join(str(tmp_dir), "bench.jsonl")
    record(bench_session(), raw)
    records = T.load(raw)
    shared = "".join(T.dump(r) + "\n" for r in T.share_records(records))
    return shared, T.timeline(T.load(shared.splitlines()))


def test_the_committed_fixtures_are_share_output_of_the_session(tmp_path):
    shared, timeline = regenerate(tmp_path)
    with open(FIXTURE, encoding="utf-8") as fh:
        assert fh.read() == shared, ("sim/tests/data/wire/bench-session.jsonl is stale: "
                                     "python3 sim/tests/test_wire_record.py --write-fixtures")
    with open(GOLDEN, encoding="utf-8") as fh:
        assert fh.read() == timeline, "the timeline golden is stale (same command)"


def test_every_committed_wire_fixture_passes_the_share_greps():
    names = sorted(os.listdir(FIXTURE_DIR))
    assert {"bench-session.jsonl", "bench-session.timeline.txt"} <= set(names), names
    for name in names:
        with open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as fh:
            text = fh.read()
        assert _leaks(text) == [], (name, _leaks(text))
        if name.endswith(".jsonl"):
            assert T.share_problems(T.load(text.splitlines())) == [], name


def test_the_timeline_shows_the_reprompt_gap_the_notifies_and_the_chunks():
    with open(FIXTURE, encoding="utf-8") as fh:
        text = T.timeline(T.load(list(fh)))
    with open(GOLDEN, encoding="utf-8") as fh:
        assert text == fh.read()
    assert ("prompt 7f3a1c2e (module FREE_CHAT): no reply; the robot asked again 20.20 s "
            "later (reprompt 9b2d4e6f) before this turn closed") in text
    assert ("reprompt 9b2d4e6f (module FREE_CHAT): chunk 0 pending +1.10 s · "
            "chunk 1 pending +1.70 s · chunk 2 done +2.40 s; 3 notify reports") in text
    assert "query schedule; answered +0.05 s" in text
    assert ("prompt 3c4d5e6f (module FREE_CHAT): done +0.90 s; actions exit_module; "
            "next request +2.00 s in module HUB") in text
    assert "eb-found-face (module HUB): result 6 NOREPLY_ACK +0.03 s" in text
    assert "1100 ms, rms 0.0300; FINAL with words +0.62 s after END_OF_SPEECH" in text
    assert "240 ms, rms 0.0040; FINAL empty (heard nothing)" in text
    assert "lost the broker (connection lost); reconnecting" in text


def test_the_replay_reproduces_the_fixtures_reply_shapes(tmp_path):
    with open(FIXTURE, encoding="utf-8") as fh:
        result = R.replay(T.load(list(fh)), data_dir=str(tmp_path))
    assert result["differences"] == [], R.report(result)
    rows = {label: (recorded, replayed) for _, label, recorded, replayed in result["rows"]}
    assert rows["prompt 7f3a1c2e"] == ([], [])
    assert rows["reprompt 9b2d4e6f"] == (["9/0", "9/1", "0/2 done"],) * 2
    assert rows["prompt 3c4d5e6f"] == (["0 [exit_module]"],) * 2
    assert rows["vision 4e5f6a7b"] == (["6"],) * 2
    assert rows["data 5a6b7c8d"] == (["0"],) * 2
    assert result["skipped"] == {"zmq": 10}


def test_the_replay_reports_a_reply_the_runtime_would_not_send(tmp_path):
    with open(FIXTURE, encoding="utf-8") as fh:
        records = T.load(list(fh))
    for rec in records:                 # the recording claims chunk 1 closed the turn
        body = rec.get("body") if rec.get("name") == "remote_chat" else None
        if isinstance(body, dict) and body.get("event_id") == E2 and body.get("chunk_num") == 1:
            body["consistency_control"] = {"is_completed": True}
    result = R.replay(records, data_dir=str(tmp_path))
    assert [d[1] for d in result["differences"]] == ["reprompt 9b2d4e6f"]
    assert "DIFFERENT" in R.report(result) and "1 difference" in R.report(result)


# --------------------------------------------------------------------------- #
# 6. The checklist's citations and the CLI's refusals
# --------------------------------------------------------------------------- #
CHECKLIST = os.path.join(REPO, "docs", "guides", "bench-day-checklist.md")


def test_every_code_symbol_the_checklist_cites_is_in_the_file_it_links():
    """`[name](path.py) `symbol``: the symbol is in that file (the doc guards check the
    links and anchors; this checks what the link is cited for)."""
    text = open(CHECKLIST, encoding="utf-8").read()
    cites = re.findall(r"\]\(([^)#]+\.py)\)\s*`([A-Za-z_][A-Za-z0-9_.]*)`", text)
    assert len(cites) >= 10, cites
    base = os.path.dirname(CHECKLIST)
    for path, symbol in cites:
        src = open(os.path.normpath(os.path.join(base, path)), encoding="utf-8").read()
        name = symbol.rsplit(".", 1)[-1]
        assert re.search(rf"\b{re.escape(name)}\b", src), f"{path} has no {symbol}"


def test_the_cli_asks_for_out_and_never_overwrites(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MOXIE_DATA_DIR", raising=False)
    assert W.main([]) == 2
    assert "--out" in capsys.readouterr().out
    taken = tmp_path / "taken.jsonl"
    taken.write_text("a recording\n")
    assert W.main(["--out", str(taken)]) == 2
    assert taken.read_text() == "a recording\n"


# --------------------------------------------------------------------------- #
# 7. SIL: a real broker, the real supervisor, the virtual robot
# --------------------------------------------------------------------------- #
def test_sil_the_recorder_hears_the_virtual_robots_echo_turn_in_order(tmp_path):
    pytest.importorskip("paho.mqtt.client", reason="the recorder is a paho client")
    import helpers_stack as S
    if not S.broker_available():
        pytest.skip("no mosquitto binary and no runnable docker: cannot boot a broker")
    from virtual_moxie import SMOKE_PROMPT, VirtualMoxie

    seen, cond = [], threading.Condition()

    def on_record(rec):
        with cond:
            seen.append(rec)
            cond.notify_all()

    def answered():
        asks = [r for r in seen if r.get("dir") == "robot>cloud" and r["name"] == "remote-chat"]
        if not asks:
            return False
        eid = asks[0]["body"].get("event_id")
        return any(r.get("dir") == "cloud>robot" and r["name"] == "remote_chat"
                   and r["body"].get("event_id") == eid and T._closing(r["body"])
                   for r in seen)

    out = tmp_path / "wire" / "sil.jsonl"
    with S.Stack(str(tmp_path / "stack")) as stack:
        rec = W.WireRecorder(str(out), on_record=on_record, say=lambda line: None)
        rec.open()
        rec.start()
        try:
            rec.connect("127.0.0.1", stack.port)
            assert rec.subscribed.wait(30), "the recorder never got its SUBACK"
            vm = VirtualMoxie("127.0.0.1", stack.port, timeout=15, verbose=False)
            assert vm.run_smoke(), vm.errors
            with cond:
                assert cond.wait_for(answered, timeout=15), \
                    f"the recorder never wrote the reply: {[r.get('name') for r in seen]}"
        finally:
            rec.close_client()
            rec.stop("the SIL turn is over")
    lines = lines_of(out)
    mine = _msgs(lines, vm.device_id)
    order = [(m["dir"], m["name"]) for m in mine]
    i_state = order.index(("robot>cloud", "state"))
    i_config = order.index(("cloud>robot", "config"))
    i_ask = order.index(("robot>cloud", "remote-chat"))
    i_reply = [i for i, o in enumerate(order) if o == ("cloud>robot", "remote_chat")]
    assert i_state < i_config < i_ask < i_reply[0], order
    ask = mine[i_ask]["body"]
    assert ask["speech"] == SMOKE_PROMPT
    replies = [mine[i]["body"] for i in i_reply]
    assert {r["event_id"] for r in replies} == {ask["event_id"]}
    assert T._closing(replies[-1])
    assert replies[-1]["output"]["text"] == f"You said: {SMOKE_PROMPT}"
    assert lines[-1]["kind"] == "stop" and lines[-1]["reason"] == "the SIL turn is over"


if __name__ == "__main__":
    if sys.argv[1:] != ["--write-fixtures"]:
        sys.exit("usage: python3 sim/tests/test_wire_record.py --write-fixtures")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        shared_text, timeline_text = regenerate(tmp)
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    with open(FIXTURE, "w", encoding="utf-8") as fh:
        fh.write(shared_text)
    with open(GOLDEN, "w", encoding="utf-8") as fh:
        fh.write(timeline_text)
    print(f"wrote {os.path.relpath(FIXTURE, REPO)} and {os.path.relpath(GOLDEN, REPO)}")
