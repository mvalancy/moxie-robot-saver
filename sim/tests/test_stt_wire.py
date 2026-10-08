"""
The robot's STT dialect on the wire (ai-seam.md §①, mqtt-and-conversation.md §3.4 / §4.3):

1. after the config push the supervisor ASKS for the microphone — a `ProtoSubscribe`
   naming `embodied.perception.audio.zmqSTTRequest` on `commands/zmq`;
2. a transcript goes back as a `zmqSTTResponse` in the bus framing
   `b'<full_name>:' + protobuf`, never JSON;
3. the ask is repeated whenever the robot's session may have lost it: a second broker
   connect line with no disconnect in between (community signal C4, "crossed ears"), a
   wake, a re-permit (or the fleet-wide toggle letting the robot in), the Listening
   picker turning the ears on, a broker outage (in whichever order the supervisor and
   the robot come back), a supervisor restart; and the settle after a connect line
   always asks, even when one of those landed inside its one-second window;
4. every way mosquitto 2.0.20 (and 1.6) says a robot left is recognised, so the robot's
   return is a fresh onboarding.

Every frame is decoded with the committed pb2 oracles under `tools/robot-toolkit`, not
with a reader written beside the writer. Hermetic: fake transport, held timers, no clock.

Field reference: OpenMoxie (MIT, proven on real robots) sends exactly this subscribe
after its config push (site/hive/mqtt/moxie_server.py:254-266, framed by
`send_zmq_to_bot` :308-310) and answers with a protobuf zmqSTTResponse
(zmq_stt_handler.py:52-76). Ceiling: no physical Moxie has heard through this appliance;
these tests prove what we SEND, not what a robot does with it.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading

import pytest

from helpers_runtime import (REPO, FakeClient, FakeInfo, deliver, http_json,  # noqa: E402
                             loopback, make_runtime, parse_zmq_frame, split_zmq_frame,
                             status_server, toolkit_pb2)
from helpers_audio import pb_zmq_stt_frame                          # noqa: E402
from moxie_sdk.app import MoxieApp                                  # noqa: E402
from moxie_sdk.store import JsonStore                               # noqa: E402
from moxie_sdk import stt                                           # noqa: E402
from moxie_sdk.stt import Transcriber                               # noqa: E402
from moxie_sdk.types import ChildProfile                            # noqa: E402
import moxie_runtime                                                # noqa: E402

Log_pb2 = toolkit_pb2("embodied.logging.Log_pb2")
zmqSTT_pb2 = toolkit_pb2("embodied.perception.audio.zmqSTT_pb2")
# The names the bus routes by, from the oracle rather than from the module under test.
PROTO_SUBSCRIBE = Log_pb2.ProtoSubscribe.DESCRIPTOR.full_name
ZMQ_STT_REQUEST = zmqSTT_pb2.zmqSTTRequest.DESCRIPTOR.full_name
ZMQ_STT_RESPONSE = zmqSTT_pb2.zmqSTTResponse.DESCRIPTOR.full_name

DEV = "d_ea15"                      # hex only: the ids `CONNECT_RE` recognises
ZMQ = "/devices/{d}/commands/zmq"
CONFIG = "/devices/{d}/config"
WAKEUP = "/devices/{d}/commands/wakeup"
LOG = "$SYS/broker/log/N"
# mosquitto 2.0.20's connect line, verbatim from a live capture (loopback addresses only).
CONNECT_LINE = "1759900000: New client connected from 127.0.0.1:51234 as {d} (p2, c1, k30)."
EPOCH_2024_MS = 1_700_000_000_000


class EchoApp(MoxieApp):
    name = "test-ears"

    def __init__(self):
        self.connected = []

    def on_connect(self, robot):
        self.connected.append(robot.device_id)

    def respond(self, turn):
        from moxie_sdk.types import Reply
        return Reply(text=f"You said: {turn.speech}")


class Ears(Transcriber):
    """A transcriber that answers a fixed line and counts how often it was asked."""
    name = "fake-ears"

    def __init__(self, text="heard you"):
        self.text, self.calls = text, 0

    def transcribe(self, pcm, sample_rate=16000):
        self.calls += 1
        return self.text


class _HeldTimer:
    """A `threading.Timer` that never fires on its own (the 1 s settle after a connect,
    the roster resume after a CONNACK); `fire(name)` runs the pending ones by function."""

    pending: list = []

    def __init__(self, delay, fn):
        self.delay, self.fn, self.daemon = delay, fn, False
        _HeldTimer.pending.append(self)

    def start(self):
        pass

    @classmethod
    def fire(cls, name="_settle"):
        held = [t for t in cls.pending if t.fn.__name__ == name]
        cls.pending = [t for t in cls.pending if t not in held]
        for t in held:
            t.fn()
        return len(held)


@pytest.fixture
def timers(monkeypatch):
    _HeldTimer.pending = []
    monkeypatch.setattr(threading, "Timer", _HeldTimer)
    return _HeldTimer


def _runtime(tmp_path, *, ears=True, allow=True, app=None):
    """A real runtime on a scratch store with a fake transport and NO robot placed: the
    robot arrives the way a real one does, through the broker's log line."""
    rt = moxie_runtime.MoxieRuntime(app=app or EchoApp(), child=ChildProfile(nickname="Sam"),
                                    allow_unverified_bots=allow,
                                    store=JsonStore(str(tmp_path)))
    rt.client = FakeClient(runtime=rt)
    if ears:
        rt.set_transcriber(Ears())
    return rt


def _connect(rt, device_id=DEV):
    deliver(rt, LOG, CONNECT_LINE.format(d=device_id))


def _state(rt, device_id=DEV):
    deliver(rt, f"/devices/{device_id}/state",
            json.dumps({"battery_level": 90, "mode": "idle"}))


def _zmq(rt, device_id=DEV) -> list:
    """Every `commands/zmq` publish to this robot — each one bytes in the bus framing
    (a JSON body fails here: nothing on that topic may be JSON)."""
    frames = rt.client.on(ZMQ.format(d=device_id))
    for f in frames:
        split_zmq_frame(f)
    return frames


def _asks(rt, device_id=DEV) -> list:
    """The ProtoSubscribe frames among them, parsed: a list of `protos` lists."""
    out = []
    for f in _zmq(rt, device_id):
        name, _ = split_zmq_frame(f)
        if name == PROTO_SUBSCRIBE:
            out.append(list(parse_zmq_frame(f, Log_pb2.ProtoSubscribe).protos))
    return out


def _order(rt, device_id=DEV) -> list:
    """The topics published to this robot, in order (zmq frames labelled by full name)."""
    out = []
    for topic, payload in rt.client.published:
        if topic == ZMQ.format(d=device_id):
            out.append(split_zmq_frame(payload)[0])
        elif topic.startswith(f"/devices/{device_id}/"):
            out.append(topic.rsplit("/", 1)[-1])
    return out


# --------------------------------------------------------------------------- #
# 1. onboarding asks for the microphone, after the config, once
# --------------------------------------------------------------------------- #

def test_the_sdk_names_the_protos_the_compiled_oracle_names():
    from moxie_sdk import stt
    assert (stt.PROTO_SUBSCRIBE, stt.ZMQ_STT_REQUEST, stt.ZMQ_STT_RESPONSE) == \
        (PROTO_SUBSCRIBE, ZMQ_STT_REQUEST, ZMQ_STT_RESPONSE)


def test_onboarding_asks_the_robot_to_stream_its_microphone(timers, tmp_path, monkeypatch):
    """The broker says a permitted robot connected; after the settle it has its config
    and then ONE `ProtoSubscribe` for `zmqSTTRequest` with a millisecond timestamp."""
    monkeypatch.setattr(stt, "now_ms", lambda: EPOCH_2024_MS + 123)
    rt = _runtime(tmp_path)
    _connect(rt)
    assert _zmq(rt) == [], "nothing goes out before the settle"
    assert timers.fire("_settle") == 1
    assert _order(rt) == ["config", PROTO_SUBSCRIBE], _order(rt)
    sub = parse_zmq_frame(_zmq(rt)[0], Log_pb2.ProtoSubscribe)
    assert list(sub.protos) == [ZMQ_STT_REQUEST]
    assert sub.timestamp == EPOCH_2024_MS + 123, "the SDK's millisecond clock, not zero"
    assert rt.app.connected == [DEV], "the greeting still follows"


def test_a_repeated_state_sends_nothing_more(timers, tmp_path):
    """`/state` repeats (it is how a real Moxie announces itself and reports): it must
    not be read as a new session."""
    rt = _runtime(tmp_path)
    _connect(rt)
    timers.fire()
    _state(rt)
    _state(rt)
    _state(rt)
    assert timers.fire() == 0
    assert _asks(rt) == [[ZMQ_STT_REQUEST]]
    assert _order(rt).count("config") == 1


def test_a_pending_robot_is_not_asked(timers, tmp_path):
    """Not permitted: the minimal config and nothing else (audio would reach a brain the
    robot is not allowed)."""
    rt = _runtime(tmp_path, allow=False)
    _connect(rt)
    timers.fire()
    assert _order(rt) == ["config"]
    assert rt.client.on(CONFIG.format(d=DEV))[-1]["pairing_status"] != "paired"
    assert rt.status_snapshot()["robots"][0]["stt_subscribed_at"] is None


def test_without_a_transcriber_nobody_is_asked(timers, tmp_path):
    """No ears installed, no ask: the audio would stream to nobody."""
    rt = _runtime(tmp_path, ears=False)
    _connect(rt)
    timers.fire()
    assert _order(rt) == ["config"]
    assert rt.status_snapshot()["robots"][0]["stt_subscribed_at"] is None


# --------------------------------------------------------------------------- #
# 2. the transcript goes back as the frame the robot parses
# --------------------------------------------------------------------------- #

def _speak(rt, device_id, uuid, *frames_audio):
    """Stream one utterance at the runtime through `events/zmq` as the robot does."""
    frames_audio = frames_audio or (b"aa", b"bb")
    deliver(rt, f"/devices/{device_id}/events/zmq",
            pb_zmq_stt_frame(1, frames_audio[0], uuid))
    for chunk in frames_audio[1:]:
        deliver(rt, f"/devices/{device_id}/events/zmq", pb_zmq_stt_frame(2, chunk, uuid))
    deliver(rt, f"/devices/{device_id}/events/zmq", pb_zmq_stt_frame(3, b"", uuid))


def test_a_final_transcript_is_a_frame_the_robot_can_parse(tmp_path):
    """END_OF_SPEECH → one `zmqSTTResponse` frame: type FINAL, the speech, the utterance
    uuid, a timestamp. Decoded with the compiled proto, not our own reader."""
    rt, dev = make_runtime(EchoApp(), device_id=DEV, store=JsonStore(str(tmp_path)))
    rt.set_transcriber(Ears("tell me a story"))
    _speak(rt, dev, "utt-7")
    frames = _zmq(rt, dev)
    assert [split_zmq_frame(f)[0] for f in frames] == [PROTO_SUBSCRIBE, ZMQ_STT_RESPONSE]
    resp = parse_zmq_frame(frames[-1], zmqSTT_pb2.zmqSTTResponse)
    assert resp.type == resp.FINAL
    assert resp.speech == "tell me a story" and resp.uuid == "utt-7"
    assert resp.timestamp > EPOCH_2024_MS
    assert resp.confidence == 1.0


def test_an_empty_transcription_is_still_a_final(tmp_path):
    """Silence or a breath transcribes to '': the robot still needs its FINAL, or its
    turn never closes."""
    rt, dev = make_runtime(EchoApp(), device_id=DEV, store=JsonStore(str(tmp_path)))
    ears = Ears("")
    rt.set_transcriber(ears)
    _speak(rt, dev, "utt-quiet", b"\x00\x00" * 4)
    resp = parse_zmq_frame(_zmq(rt, dev)[-1], zmqSTT_pb2.zmqSTTResponse)
    assert ears.calls == 1
    assert resp.type == resp.FINAL and resp.speech == "" and resp.uuid == "utt-quiet"


class FlakyEars(Transcriber):
    """A bare local engine that raises on its first utterance (a model that failed to
    load, a CUDA error) and hears the next one."""
    name = "flaky-ears"

    def __init__(self):
        self.calls = 0

    def transcribe(self, pcm, sample_rate=16000):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("model not loaded")
        return "ok now"


def test_a_transcriber_that_fails_still_sends_the_robot_its_final(tmp_path):
    """The robot is waiting on a FINAL; without one its turn never closes. An engine
    that raises gets it a FINAL with no speech and the failure in the recovered error
    fields (zmqSTT.proto `error_code=8`, `error_message=9`), the way the field-proven
    server answers a failed transcription (OpenMoxie `zmq_stt_handler.py:70-73`,
    `error_code=66` + the exception text). The next utterance is heard normally."""
    rt, dev = make_runtime(EchoApp(), device_id=DEV, store=JsonStore(str(tmp_path)))
    ears = FlakyEars()
    rt.set_transcriber(ears)
    _speak(rt, dev, "utt-err")
    frames = _zmq(rt, dev)
    assert [split_zmq_frame(f)[0] for f in frames] == [PROTO_SUBSCRIBE, ZMQ_STT_RESPONSE], \
        "the robot got no FINAL after its engine failed"
    resp = parse_zmq_frame(frames[-1], zmqSTT_pb2.zmqSTTResponse)
    assert resp.type == resp.FINAL and resp.uuid == "utt-err" and resp.speech == ""
    assert resp.error_code == stt.STT_ERROR_CODE == 66
    assert "RuntimeError" in resp.error_message and "model not loaded" in resp.error_message
    assert resp.timestamp > EPOCH_2024_MS
    assert any(r["kind"] == "error" for r in rt.recent), "the console feed is told"

    _speak(rt, dev, "utt-next")                # the session was reset: heard normally
    resp = parse_zmq_frame(_zmq(rt, dev)[-1], zmqSTT_pb2.zmqSTTResponse)
    assert (resp.speech, resp.uuid, resp.error_code, resp.error_message) == \
        ("ok now", "utt-next", 0, "")
    assert ears.calls == 2


def test_nothing_on_the_zmq_topic_is_ever_json(timers, tmp_path):
    """The robot injects `commands/zmq` straight onto its bus as `name:bytes`; a JSON
    body there is a frame it cannot route. One session end to end: not one."""
    rt = _runtime(tmp_path)
    _connect(rt)
    timers.fire()
    _speak(rt, DEV, "utt-1")
    rt.wake_robot(DEV)
    rt.set_permit(DEV, True)
    for payload in _zmq(rt):
        assert isinstance(payload, bytes)
        assert payload.startswith(b"embodied.")
        with pytest.raises(Exception):
            json.loads(payload)
    assert len(_zmq(rt)) == 4


# --------------------------------------------------------------------------- #
# 3. the ask is repeated whenever the robot may have lost it
# --------------------------------------------------------------------------- #

def test_a_second_connect_line_with_no_goodbye_onboards_again(timers, tmp_path):
    """C4's mechanism. A robot drops Wi-Fi and comes back before the broker noticed (or
    leaves with a line we do not know): the only sign is a SECOND connect line. It must
    get its config and its microphone ask again, not be ignored as 'already onboarded'."""
    rt = _runtime(tmp_path)
    _connect(rt)
    timers.fire()
    assert _order(rt) == ["config", PROTO_SUBSCRIBE]
    history_before = rt.history[DEV]

    _connect(rt)                               # same robot, no disconnect line between
    assert timers.fire() == 1, "the second connect line scheduled no onboarding"
    assert _order(rt) == ["config", PROTO_SUBSCRIBE, "config", PROTO_SUBSCRIBE]
    assert _asks(rt) == [[ZMQ_STT_REQUEST]] * 2
    assert DEV in rt._seen_since_connect
    assert rt.history[DEV] is history_before, "the conversation survived the blip"
    assert rt.app.connected == [DEV, DEV]


def test_waking_a_robot_asks_again(tmp_path):
    """A sleeping robot drops its subscriptions (upstream openmoxie PR #59): the wakeup
    command is followed by a fresh ask, exactly once, after the wakeup itself."""
    rt, dev = make_runtime(EchoApp(), device_id=DEV, store=JsonStore(str(tmp_path)))
    rt.set_transcriber(Ears())
    assert len(_asks(rt)) == 1                 # the Listening engine asked on install
    out = rt.wake_robot(dev)
    assert out["ok"] and out["published"], out
    assert _order(rt)[-2:] == ["wakeup", PROTO_SUBSCRIBE]
    assert len(_asks(rt)) == 2


def test_a_wake_that_was_not_published_asks_nothing(tmp_path):
    """No socket: the wake is refused, and no ask is attempted (or counted as a drop)."""
    rt, dev = make_runtime(EchoApp(), device_id=DEV, store=JsonStore(str(tmp_path)))
    rt.set_transcriber(Ears())
    rt.client.drop()
    assert rt.wake_robot(dev)["ok"] is False
    assert len(_asks(rt)) == 1                 # only the install-time ask
    assert rt.publish_drops == 0


def test_permitting_a_robot_asks_it(timers, tmp_path):
    """A pending robot is let in: full config, then the ask, then the greeting. A second
    Permit asks again (the parent's click is a 'make it work' button)."""
    rt = _runtime(tmp_path, allow=False)
    _connect(rt)
    timers.fire()
    assert _order(rt) == ["config"]

    rt.set_permit(DEV, True, label="Sam's Moxie")
    assert _order(rt) == ["config", "config", PROTO_SUBSCRIBE]
    assert rt.client.on(CONFIG.format(d=DEV))[-1]["pairing_status"] == "paired"
    assert rt.app.connected == [DEV]
    rt.set_permit(DEV, True)
    assert len(_asks(rt)) == 2
    rt.set_permit(DEV, False)                  # revoked: the minimal config, no ask
    assert len(_asks(rt)) == 2
    assert _order(rt)[-1] == "config"


def _summary(robot: dict) -> str:
    sys.path.insert(0, os.path.join(REPO, "server"))
    from moxie_server.fleet.robots import robot_summary
    return robot_summary(robot)


def test_a_revoked_robot_shows_no_mic_asked(timers, tmp_path):
    """Nothing withdraws a `ProtoSubscribe` (the recovered `Log.proto` has no such
    message), so a revoked robot may well keep streaming to the broker, where the permit
    gate drops the audio. But it is pending now, and a pending robot is never asked: the
    record of the ask goes with the permit, so `/status` and the card stop saying
    `mic asked`. A re-permit asks again and records it."""
    rt = _runtime(tmp_path, allow=False)
    _connect(rt)
    timers.fire()
    rt.set_permit(DEV, True)
    assert rt.status_snapshot()["robots"][0]["stt_subscribed_at"]

    rt.set_permit(DEV, False)
    robot = rt.status_snapshot()["robots"][0]
    assert robot["pending"] is True
    assert robot["stt_subscribed_at"] is None, "a pending robot is never 'mic asked'"
    assert "mic asked" not in _summary(robot) and "pending" in _summary(robot)
    assert len(_asks(rt)) == 1 and _order(rt)[-1] == "config"

    rt.set_permit(DEV, True)
    assert len(_asks(rt)) == 2
    assert rt.status_snapshot()["robots"][0]["stt_subscribed_at"]


def test_listening_off_keeps_the_ask_on_record(timers, tmp_path):
    """Listening `off` installs no engine. The robot was asked and, as far as the
    supervisor knows, still streams (nothing withdraws the ask); the record stands, so
    the engine installed next finds the robot asked and does not ask again. Pinned as
    documented in mqtt-and-conversation.md §3.4."""
    rt = _runtime(tmp_path)
    _connect(rt)
    timers.fire()
    rt.set_transcriber(None)                   # the picker's `off`
    assert rt.status_snapshot()["robots"][0]["stt_subscribed_at"]
    rt.set_transcriber(Ears())
    assert len(_asks(rt)) == 1


def test_the_fleet_wide_toggle_onboards_the_robots_it_lets_in(timers, tmp_path, monkeypatch):
    """`allow_unverified_bots` flipped on from the console lets every pending robot in
    at once. Each must be onboarded the way a Permit does (full config, then the mic
    ask, then the greeting), not left deaf until a wake or a reconnect. A robot that was
    already in gets only the config re-push. Flipped off: the minimal config again, and
    no `mic asked` on a robot that is pending again."""
    monkeypatch.delenv("MOXIE_ALLOW_UNVERIFIED_BOTS", raising=False)
    rt = _runtime(tmp_path, allow=None)        # the durable flag governs
    _connect(rt, "d_a1")
    _connect(rt, "d_b2")
    assert timers.fire() == 2
    rt.set_permit("d_a1", True)                # one is let in by hand
    assert _asks(rt, "d_a1") == [[ZMQ_STT_REQUEST]] and _asks(rt, "d_b2") == []
    assert rt.app.connected == ["d_a1"]

    rt.set_allow_unverified_bots(True)
    assert _order(rt, "d_b2") == ["config", "config", PROTO_SUBSCRIBE], _order(rt, "d_b2")
    assert rt.client.on(CONFIG.format(d="d_b2"))[-1]["pairing_status"] == "paired"
    assert rt.app.connected == ["d_a1", "d_b2"], "the toggle greets the robot it let in"
    assert len(_asks(rt, "d_a1")) == 1 and _order(rt, "d_a1")[-1] == "config"
    snap = {r["device_id"]: r for r in rt.status_snapshot()["robots"]}
    assert snap["d_b2"]["pending"] is False and snap["d_b2"]["stt_subscribed_at"]

    rt.set_allow_unverified_bots(False)
    snap = {r["device_id"]: r for r in rt.status_snapshot()["robots"]}
    assert snap["d_b2"]["pending"] is True and snap["d_b2"]["stt_subscribed_at"] is None
    assert snap["d_a1"]["stt_subscribed_at"], "the hand-permitted robot is still in"
    assert len(_asks(rt, "d_b2")) == 1 and _order(rt, "d_b2")[-1] == "config"
    assert rt.client.on(CONFIG.format(d="d_b2"))[-1]["pairing_status"] != "paired"


def test_the_listening_picker_asks_the_robots_not_yet_asked(timers, tmp_path):
    """Ears turned on after the robots connected (the console's Listening picker):
    every connected, permitted robot not yet asked this session is asked now; one that
    was asked is left alone, and a pending one is never asked."""
    rt = _runtime(tmp_path, ears=False, allow=False)
    for d in ("d_a1", "d_b2", "d_de4d"):
        _connect(rt, d)
    assert timers.fire() == 3                  # config only: no ears yet
    rt.set_permit("d_a1", True)
    rt.set_permit("d_b2", True)
    assert all(_asks(rt, d) == [] for d in ("d_a1", "d_b2", "d_de4d"))

    rt.set_transcriber(Ears())
    assert _asks(rt, "d_a1") == [[ZMQ_STT_REQUEST]]
    assert _asks(rt, "d_b2") == [[ZMQ_STT_REQUEST]]
    assert _asks(rt, "d_de4d") == []

    rt.set_transcriber(Ears("another engine"))  # a swap: the robots are still streaming
    assert len(_asks(rt, "d_a1")) == 1 and len(_asks(rt, "d_b2")) == 1

    _connect(rt, "d_c3")                       # a robot that arrives after the pick
    timers.fire()
    assert _asks(rt, "d_c3") == []             # (pending until permitted)
    rt.set_permit("d_c3", True)
    assert _asks(rt, "d_c3") == [[ZMQ_STT_REQUEST]]


def test_the_listening_picker_also_asks_a_ghost_without_recording_it(timers, tmp_path):
    """A robot served before OUR socket dropped and silent since (a ghost) may well
    still be connected: the broker log is live-only, so it never announces itself
    again, and leaving it out would leave it deaf. The picker asks it too (one QoS 0
    message to nobody if it is gone) but records nothing, so its own return is still
    asked (`test_an_ask_sent_while_the_robot_was_away_does_not_silence_its_return`).
    The deliberate choice `set_transcriber` documents."""
    rt = _runtime(tmp_path, ears=False)
    rt.client.up()
    _connect(rt)
    timers.fire()                              # config only: no ears yet
    rt.client.drop()                           # our blip; the robot said nothing
    rt.client.up()
    assert rt.status_snapshot()["robots"][0]["seen_since_connect"] is False
    rt.set_transcriber(Ears())
    assert _asks(rt) == [[ZMQ_STT_REQUEST]], "the ghost was asked"
    assert rt.status_snapshot()["robots"][0]["stt_subscribed_at"] is None, "not recorded"


def test_a_broker_outage_clears_the_latch_so_the_next_onboarding_asks_again(timers, tmp_path):
    """Our socket died: the robot's session is unknown, the belief is dropped, and its
    next evidence (a `/state`) is a fresh onboarding with a fresh ask."""
    rt = _runtime(tmp_path)
    rt.client.up()
    _connect(rt)
    timers.fire()
    assert rt.status_snapshot()["robots"][0]["stt_subscribed_at"]

    rt.client.drop()
    assert rt.status_snapshot()["robots"][0]["stt_subscribed_at"] is None
    rt.client.up()
    _state(rt)                                 # the robot's first word after the gap
    assert timers.fire("_settle") == 1
    assert _asks(rt) == [[ZMQ_STT_REQUEST]] * 2
    assert _order(rt)[-2:] == ["config", PROTO_SUBSCRIBE]


def _resume_fires(rt, timers):
    assert timers.fire("_resume") == 1, "no roster resume was pending after the CONNACK"


def _listening_picked(rt, timers):
    rt.set_transcriber(Ears("swapped"))        # any voice save rebinds the Listening engine


def _woken(rt, timers):
    assert rt.wake_robot(DEV)["published"]


def _permitted_again(rt, timers):
    rt.set_permit(DEV, True)


@pytest.mark.parametrize("during_the_gap", [
    pytest.param(_resume_fires, id="roster-resume"),
    pytest.param(_listening_picked, id="listening-pick"),
    pytest.param(_woken, id="wake"),
    pytest.param(_permitted_again, id="permit"),
])
def test_an_ask_sent_while_the_robot_was_away_does_not_silence_its_return(
        timers, tmp_path, during_the_gap):
    """The ordinary broker-restart order: the supervisor (on the broker's host) reconnects
    first, the robot is still on its Wi-Fi backoff. The roster resume fires 1 s after our
    CONNACK (on by default), or a parent picks Listening, wakes or re-permits the robot,
    and an ask goes out to nobody. That ask must not count as the robot's session: when
    its own connect line arrives it gets its config and exactly one ask, and `/status`
    says `stt_subscribed_at` only then."""
    rt = _runtime(tmp_path)
    rt.client.up()
    timers.fire("_resume")                     # the first CONNACK's resume: nothing rostered
    _connect(rt)
    timers.fire("_settle")
    assert len(_asks(rt)) == 1

    rt.client.drop()                           # the broker restarts: both sockets die
    rt.client.up()                             # ours is back first; the robot is a ghost
    during_the_gap(rt, timers)
    robot = rt.status_snapshot()["robots"][0]
    assert robot["seen_since_connect"] is False
    assert robot["stt_subscribed_at"] is None, "an ask to a ghost is not its session"
    mark = len(rt.client.published)

    _connect(rt)                               # the robot's own new session, later
    assert timers.fire("_settle") == 1
    since = [split_zmq_frame(p)[0] if t == ZMQ.format(d=DEV) else t.rsplit("/", 1)[-1]
             for t, p in rt.client.published[mark:] if t.startswith(f"/devices/{DEV}/")]
    assert since == ["config", PROTO_SUBSCRIBE], since
    assert rt.status_snapshot()["robots"][0]["stt_subscribed_at"], "asked, and recorded"


def test_a_listening_pick_inside_the_settle_window_does_not_rob_the_settle_of_its_ask(
        timers, tmp_path):
    """A parent saves a voice pick in the second between a robot's connect line and its
    settle. That ask goes out before the config push, and possibly before the robot has
    re-subscribed to its command topics (the reason the settle waits). The settle must
    still ask after its config push: at most one redundant QoS 0 message."""
    rt = _runtime(tmp_path, ears=False)
    _connect(rt)
    rt.set_transcriber(Ears())                 # inside the window: before the settle
    assert _order(rt) == [PROTO_SUBSCRIBE], "the pick asked the connected robot at once"
    assert timers.fire("_settle") == 1
    assert _order(rt) == [PROTO_SUBSCRIBE, "config", PROTO_SUBSCRIBE], _order(rt)
    assert rt.status_snapshot()["robots"][0]["stt_subscribed_at"]


def test_a_wake_inside_the_settle_window_does_not_rob_the_settle_of_its_ask(timers, tmp_path):
    """The same second, a parent presses Wake: the wakeup's ask lands before the config
    push; the settle's own ask still follows the config."""
    rt = _runtime(tmp_path)
    _connect(rt)
    assert rt.wake_robot(DEV)["published"]
    assert _order(rt) == ["wakeup", PROTO_SUBSCRIBE]
    assert timers.fire("_settle") == 1
    assert _order(rt) == ["wakeup", PROTO_SUBSCRIBE, "config", PROTO_SUBSCRIBE], _order(rt)


def test_a_sub_second_flap_is_asked_by_both_settles(timers, tmp_path):
    """A robot whose second connect line arrives before its first settle fired: the
    stale settle asks the (rebuilt) session, and the new settle must not take that as
    its own ask. Each settle ends with config, then the ask."""
    rt = _runtime(tmp_path)
    _connect(rt)
    _connect(rt)                               # no disconnect line, inside the window
    assert timers.fire("_settle") == 2
    assert _order(rt) == ["config", PROTO_SUBSCRIBE] * 2, _order(rt)


def test_a_supervisor_restart_asks_the_robots_in_the_roster(timers, tmp_path):
    """A robot that sat connected through our restart was asked by a process that is
    gone. The roster resume re-pushes config to it with no robot action; the ask rides
    along."""
    rt = _runtime(tmp_path)
    _connect(rt)
    timers.fire()

    rt2 = _runtime(tmp_path)                   # same store, fresh process
    assert rt2.resume_roster() == [DEV]
    assert _order(rt2) == ["config", PROTO_SUBSCRIBE]
    assert _asks(rt2) == [[ZMQ_STT_REQUEST]]


def test_a_robot_the_broker_says_left_and_returned_is_onboarded_again(timers, tmp_path):
    """The lines mosquitto 2.0.20 really prints: a keepalive expiry, then the robot's
    new session. The first forgets the robot; the second onboards it from scratch."""
    rt = _runtime(tmp_path)
    _connect(rt)
    timers.fire()
    deliver(rt, LOG, f"1759900030: Client {DEV} has exceeded timeout, disconnecting.")
    assert DEV not in rt.robots
    _connect(rt)
    timers.fire()
    assert _order(rt) == ["config", PROTO_SUBSCRIBE, "config", PROTO_SUBSCRIBE]


def test_a_robot_whose_new_socket_displaced_its_old_one_is_onboarded_again(timers, tmp_path):
    """The same client id connects again while its old session is open: mosquitto logs
    `already connected, closing old connection` (at level E) and then the new connect
    line. Either line alone is enough; together they must not double-onboard."""
    rt = _runtime(tmp_path)
    _connect(rt)
    timers.fire()
    deliver(rt, "$SYS/broker/log/E",
            f"1759900040: Client {DEV} already connected, closing old connection.")
    assert DEV not in rt.robots
    _connect(rt)
    assert timers.fire() == 1
    assert _order(rt) == ["config", PROTO_SUBSCRIBE, "config", PROTO_SUBSCRIBE]


# --------------------------------------------------------------------------- #
# 4. every way the broker says a robot left
# --------------------------------------------------------------------------- #

# Verbatim from the `mosquitto` binary of eclipse-mosquitto:2.0.20 (`strings`) and from a
# live capture of its `$SYS/broker/log` (2026-10-08); the last from eclipse-mosquitto:1.6.15.
LEAVE_LINES = [
    "1759900000: Client d_1234abcd disconnected.",
    "1759900000: Client d_1234abcd closed its connection.",
    "1759900000: Client d_1234abcd has exceeded timeout, disconnecting.",
    "1759900000: Client d_1234abcd already connected, closing old connection.",
    "1759900000: Client d_1234abcd disconnected due to protocol error.",
    "1759900000: Client d_1234abcd disconnected due to malformed packet.",
    "1759900000: Client d_1234abcd disconnected, not authorised.",
    "1759900000: Client d_1234abcd disconnected: Connection reset by peer.",
    "1759900000: Client d_1234abcd been disconnected by administrative action.",
    "1759900000: Bad socket read/write on client d_1234abcd: Connection lost",
    "1759900000: Socket error on client d_1234abcd, disconnecting.",
]
NOT_LEAVE_LINES = [
    "1759900000: New connection from 127.0.0.1:51234 on port 1883.",
    "1759900000: New client connected from 127.0.0.1:51234 as d_1234abcd (p2, c1, k30).",
    "1759900000: New client connected from 127.0.0.1:51234 as d_1234abcd (p2, c1, k30, u'robot').",
    "1759900000: Client <unknown> disconnected due to protocol error.",
    "1759900000: Client supervisor disconnected.",
]


@pytest.mark.parametrize("line", LEAVE_LINES)
def test_every_way_mosquitto_says_a_robot_left_is_recognised(line):
    m = moxie_runtime.DISCONNECT_RE.search(line)
    assert m, line
    assert next(g for g in m.groups() if g) == "d_1234abcd"
    assert not moxie_runtime.CONNECT_RE.search(line)


@pytest.mark.parametrize("line", NOT_LEAVE_LINES)
def test_a_connect_or_a_stranger_is_not_a_robot_leaving(line):
    assert not moxie_runtime.DISCONNECT_RE.search(line), line


def test_the_old_pattern_missed_the_lines_a_sleeping_robot_produces():
    """Pinned: the pattern this replaced matched only two spellings. A robot that slept
    through its keepalive produced `has exceeded timeout` and was never forgotten, so its
    return was never a fresh onboarding — a crossed-ears mechanism (one of the plausible
    ones; none is verified on hardware)."""
    old = re.compile(r"Client (d_[a-f0-9-]+) (?:closed its connection|disconnected)", re.I)
    missed = [l for l in LEAVE_LINES if not old.search(l)]
    assert len(missed) == 5, missed
    assert all(moxie_runtime.DISCONNECT_RE.search(l) for l in missed)


# --------------------------------------------------------------------------- #
# 5. the transport, the status, the console
# --------------------------------------------------------------------------- #

def test_a_bytes_publish_with_no_socket_is_a_counted_drop(tmp_path):
    rt, dev = make_runtime(EchoApp(), device_id=DEV, store=JsonStore(str(tmp_path)))
    rt.client.drop()
    frame = b"embodied.logging.ProtoSubscribe:\x08\x01"
    ok, why = rt._publish(ZMQ.format(d=dev), frame, device_id=dev, what="stt_subscribe")
    assert ok is False and why == rt.NO_BROKER_REASON
    assert rt.publish_drops == 1
    # refused before the write, like text: the transport never saw it
    assert rt.client.published == [] and rt.client.dropped == []
    assert rt.conn_events()[-1]["topic"] == ZMQ.format(d=dev)


def test_a_bytes_publish_goes_out_untouched(tmp_path):
    """`_publish` must not JSON-encode bytes (a quoted base64 string is not a frame)."""
    rt, dev = make_runtime(EchoApp(), device_id=DEV, store=JsonStore(str(tmp_path)))
    frame = bytearray(b"embodied.logging.ProtoSubscribe:\x08\x01")
    assert rt._publish(ZMQ.format(d=dev), frame) == (True, "")
    assert rt.client.published == [(ZMQ.format(d=dev), bytes(frame))]


#: paho's `MQTT_ERR_QUEUE_SIZE`: the socket is up but the outbound queue refused the message.
MQTT_ERR_QUEUE_SIZE = 15


class RefusingClient(FakeClient):
    """A transport whose socket is up but whose publish is refused (paho returns a non-zero
    rc, e.g. its outbound queue is full): the message never left the box."""

    def publish(self, topic, payload):
        self.dropped.append((topic, payload))
        return FakeInfo(MQTT_ERR_QUEUE_SIZE)


def test_an_ask_the_transport_refused_is_not_recorded(timers, tmp_path):
    """The socket is up, the robot is confirmed on this connection, and paho refuses the
    publish. The robot never heard that ask, so `/status` must not say it did (the
    console would show `mic asked` for a deaf robot); once the transport takes the
    next ask, it is recorded."""
    rt = _runtime(tmp_path, ears=False)
    _connect(rt)
    timers.fire()                              # config only: no ears yet
    rt.client = RefusingClient(runtime=rt)
    rt.set_transcriber(Ears())                 # the ask, at a CONFIRMED robot
    assert rt.client.published == [] and len(rt.client.dropped) == 1
    assert split_zmq_frame(rt.client.dropped[0][1])[0] == PROTO_SUBSCRIBE
    assert rt.publish_drops == 1
    assert rt.status_snapshot()["robots"][0]["stt_subscribed_at"] is None, \
        "a refused publish is not an ask the robot heard"

    rt.client = FakeClient(runtime=rt)         # the transport takes it now
    rt.set_transcriber(Ears())
    assert _asks(rt) == [[ZMQ_STT_REQUEST]]
    assert rt.status_snapshot()["robots"][0]["stt_subscribed_at"]


def test_status_carries_when_the_robot_was_asked_and_the_console_says_so(timers, tmp_path):
    """`/status` robots carry `stt_subscribed_at` (an ask, never an ack), and the console
    summary line says so in words a parent can read."""
    sys.path.insert(0, os.path.join(REPO, "server"))
    from moxie_server.fleet.robots import normalize_robot, robot_summary
    rt = _runtime(tmp_path)
    _connect(rt)
    timers.fire()
    base = status_server(rt)
    robot = http_json(f"{base}/status")["robots"][0]
    assert robot["device_id"] == DEV
    asked = robot["stt_subscribed_at"]
    assert isinstance(asked, float) and asked > EPOCH_2024_MS / 1000, "epoch seconds"
    line = robot_summary(robot)
    # The zone is named (the server's clock, UTC in the appliance container): a bare
    # `HH:MM` would read as the parent's local time.
    assert re.search(r"mic asked \d\d:\d\d \S+", line), line
    assert re.search(r"mic asked \d\d:\d\d \S+", normalize_robot(robot)["summary"])
    assert "listening" not in line, "an ask is not an acknowledgement"
    assert robot_summary({}) == "connected"
    assert "mic asked" not in robot_summary({"stt_subscribed_at": None})


def test_the_card_tells_the_time_of_the_ask_in_the_robots_own_zone():
    """`mic asked HH:MM` is read by a parent at home, and the appliance container runs
    on UTC with no TZ set. So the time is shown in the robot's configured `timezone_id`
    (the house's zone, from the config the robot was pushed) when the record carries
    one, else labelled `UTC`; never the server's unlabelled local time. A zone this box
    cannot resolve falls back to the labelled UTC rather than failing the card."""
    asked = 1_700_000_000.0                    # 2023-11-14 22:13:20 UTC
    assert "mic asked 22:13 UTC" in _summary({"stt_subscribed_at": asked})
    assert "mic asked 22:13 UTC" in _summary({"stt_subscribed_at": asked,
                                              "config_effective": {}})
    assert "mic asked 14:13 PST" in _summary(
        {"stt_subscribed_at": asked, "config_effective": {"timezone_id": "America/Los_Angeles"}})
    assert "mic asked 07:13 JST" in _summary(
        {"stt_subscribed_at": asked, "config_effective": {"timezone_id": "Asia/Tokyo"}})
    for bad in ("Mars/Olympus_Mons", "", None, 7):
        assert "mic asked 22:13 UTC" in _summary(
            {"stt_subscribed_at": asked, "config_effective": {"timezone_id": bad}}), bad
    assert "mic asked" not in _summary({"stt_subscribed_at": None,
                                        "config_effective": {"timezone_id": "Asia/Tokyo"}})


# --------------------------------------------------------------------------- #
# 6. loopback: a robot that streams only once asked, and hears the answer
# --------------------------------------------------------------------------- #

class RobotDouble:
    """The least a robot needs here: it streams its microphone ONLY once asked (a
    `ProtoSubscribe` naming `zmqSTTRequest`) and keeps every `zmqSTTResponse` it is
    handed. Everything it reads is decoded with the pb2 oracles."""

    def __init__(self, device_id, utterance_frames):
        self.device_id, self._frames = device_id, list(utterance_frames)
        self.client = None                     # set by `loopback`
        self.asked, self.heard, self.config = [], [], []
        self.configs_when_asked = None

    def _on_message(self, c, u, msg):
        if msg.topic == f"/devices/{self.device_id}/config":
            self.config.append(json.loads(msg.payload))
            return
        if msg.topic != f"/devices/{self.device_id}/commands/zmq":
            return
        name, body = split_zmq_frame(msg.payload)
        if name == PROTO_SUBSCRIBE:
            sub = Log_pb2.ProtoSubscribe()
            sub.ParseFromString(body)
            self.asked.append(list(sub.protos))
            self.configs_when_asked = len(self.config)
            if ZMQ_STT_REQUEST in sub.protos:
                for frame in self._frames:
                    self.client.publish(f"/devices/{self.device_id}/events/zmq", frame)
        elif name == ZMQ_STT_RESPONSE:
            resp = zmqSTT_pb2.zmqSTTResponse()
            resp.ParseFromString(body)
            self.heard.append(resp)


def test_loopback_a_robot_streams_only_once_asked_and_hears_its_transcript(timers, tmp_path):
    """Robot double ↔ real runtime, in process, on the real topics: the double says
    nothing until the ask arrives (after its config), then streams one utterance and is
    answered with a FINAL it can parse. On a runtime that never asks, it never speaks."""
    frames = [pb_zmq_stt_frame(1, b"\x01\x02" * 8, "utt-loop"),
              pb_zmq_stt_frame(2, b"\x03\x04" * 8, "utt-loop"),
              pb_zmq_stt_frame(3, b"", "utt-loop")]
    robot = RobotDouble(DEV, frames)
    rt = _runtime(tmp_path, ears=False)
    loopback(rt, robot)
    ears = Ears("I want to draw a dragon")
    rt.set_transcriber(ears)

    _connect(rt)
    assert robot.asked == [] and robot.heard == []
    timers.fire()
    assert robot.asked == [[ZMQ_STT_REQUEST]], "the robot was never asked for its microphone"
    assert robot.configs_when_asked == 1, "the ask came before the config"
    assert ears.calls == 1
    assert len(robot.heard) == 1
    final = robot.heard[0]
    assert final.type == final.FINAL
    assert final.speech == "I want to draw a dragon" and final.uuid == "utt-loop"
