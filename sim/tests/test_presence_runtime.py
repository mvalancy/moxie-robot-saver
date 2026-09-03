"""
Presence in the runtime — the robot's own eyes reaching the turn loop.

The recovered contract delivers a subscribed perception event as the `speech` of an
ordinary `RemoteChatRequest` (docs/architecture/vision.md §1.1; OpenMoxie
`doc/RemoteModuleAPI.md` §Event Handling, MIT), and requires the brain to answer it. So
everything here drives real `events/remote-chat` payloads through the real
`MoxieRuntime` over a fake transport — the same harness the other turn-loop suites use.

Hermetic: no sleeps, no broker, no model. Elapsed time is expressed by *seeding* the
presence record (a robot that went out of sight N seconds ago), never by waiting.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from helpers_runtime import (CountingSynth, LatchClient, drive_turn,   # noqa: E402
                             make_runtime)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
from moxie_sdk import presence as P                                    # noqa: E402
from moxie_sdk.app import MoxieApp                                     # noqa: E402
from moxie_sdk.types import Reply                                      # noqa: E402

FOUND, LOST = P.FOUND_FACE, P.LOST_TARGET
TTS_TOPIC = "/devices/{}/commands/tts"


class EchoApp(MoxieApp):
    name = "echo-presence"

    def __init__(self):
        self.turns = []
        self.events = []

    def respond(self, turn):
        self.turns.append(turn)
        return Reply(text=f"You said: {turn.speech}")

    def on_event(self, robot, name, payload):
        self.events.append((name, dict(payload or {})))


def _runtime(app=None, *, greet_after_s=300.0, **kw):
    rt, dev = make_runtime(app or EchoApp(), **kw)
    rt.greet_after_s = greet_after_s
    return rt, dev


def _seed_absent(rt, dev, away_s, *, greeted=False):
    """Put this robot's presence where it would be `away_s` seconds after a departure."""
    robot = rt.robots[dev]
    now = time.time()
    state = P.new_state()
    state.update({"face_present": False, "announced": "left",
                  "last_seen_at": now - away_s - 30.0,
                  "present_since": now - away_s - 60.0,
                  "last_lost_at": now - away_s, "absent_since": now - away_s,
                  "faces_seen": 1, "events": 2})
    if greeted:
        state["greeted_at"] = now - away_s + 0.1
    robot.extra["presence"] = state
    return state


def _fresh_pool(rt):
    """`drive_turn` shuts the worker pool down when it drains it, so a test that drives
    a SECOND turn through the same runtime needs a live one."""
    from concurrent.futures import ThreadPoolExecutor
    rt._pool = ThreadPoolExecutor(max_workers=4)
    return rt


def _vision(rt, dev, name, *, event_id="evt-eye", input_vars=None):
    """Publish one vision event as the robot does, and return the response."""
    return drive_turn(rt, dev, name, event_id=event_id,
                      **({"input_vars": input_vars} if input_vars else {}))


# --------------------------------------------------------------------------- #
# 1. Ingest — the events land on RobotContext, and never on the brain
# --------------------------------------------------------------------------- #
def test_a_vision_event_updates_presence_and_never_reaches_the_brain():
    app = EchoApp()
    rt, dev = _runtime(app)
    resp = _vision(rt, dev, FOUND)
    assert app.turns == [], "eb-found-face is not something a child said"
    assert resp["result"] == "NOREPLY_ACK", resp
    assert resp["event_id"] == "evt-eye"
    assert (resp["output"]["text"] or "") == ""
    state = rt.robots[dev].extra["presence"]
    assert state["face_present"] is True and state["faces_seen"] == 1


def test_the_contract_still_gets_an_answer_because_it_requires_one():
    """"The remote module must produce some response for this input to continue the
    interaction" — so silence is not an option; `NOREPLY_ACK` (ResultCode 6) is the
    contract's own "acknowledged, no spoken line"."""
    rt, dev = _runtime()
    resp = _vision(rt, dev, LOST)
    assert resp["command"] == "remote_chat" and resp["result"] == "NOREPLY_ACK"


def test_a_vision_event_is_not_written_into_conversation_history():
    rt, dev = _runtime()
    _vision(rt, dev, FOUND)
    assert rt.history.get(dev, []) == []


def test_the_app_event_hook_sees_every_vision_event():
    app = EchoApp()
    rt, dev = _runtime(app)
    _vision(rt, dev, FOUND)
    assert app.events and app.events[0][0] == FOUND


def test_a_qr_event_carries_its_value_through_to_presence():
    rt, dev = _runtime()
    _vision(rt, dev, P.QR_EVENT, input_vars={"$eb_qr_value": "GO<launch:DM>"})
    assert rt.robots[dev].extra["presence"]["qr"]["value"] == "GO<launch:DM>"


def test_a_vision_event_on_its_own_events_subtopic_is_routed_too():
    """Defensive extra: the recovered contract uses the chat path, but a robot that
    published `events/eb-found-face` must not be ignored."""
    import json
    app = EchoApp()
    rt, dev = _runtime(app)
    rt._on_event(dev, FOUND, json.dumps({}))
    assert rt.robots[dev].extra["presence"]["face_present"] is True


# --------------------------------------------------------------------------- #
# 2. The subscription — without it a real robot sends us nothing
# --------------------------------------------------------------------------- #
def test_the_first_reply_subscribes_the_robot_to_its_own_vision_events():
    rt, dev = _runtime()
    resp = drive_turn(rt, dev, "hello")
    ra = resp["response_actions"]
    sub = ra[0]["event_subscription"]
    assert sub["clear"] is False
    for name in (FOUND, LOST, "eb-qr-event", "eb-dr-event", "eb-br-event"):
        assert name in sub["active"], sub
    assert resp["response_action"]["event_subscription"] == sub, "legacy singular mirrored"


def test_the_subscription_is_sent_once_per_module_not_once_per_turn():
    rt, dev = _runtime()
    drive_turn(rt, dev, "hello", event_id="e1")
    _fresh_pool(rt)
    second = drive_turn(rt, dev, "again", event_id="e2")
    assert "response_actions" not in second, second


def test_an_unpermitted_robot_is_never_subscribed():
    rt, dev = _runtime(allow_unverified_bots=False)
    assert rt._vision_subscription(dev) is None


def test_the_subscription_can_be_turned_off():
    rt, dev = _runtime()
    rt.vision = False
    resp = drive_turn(rt, dev, "hello")
    assert "response_actions" not in resp


# --------------------------------------------------------------------------- #
# 3. The greeting — the delight, and every gate on it
# --------------------------------------------------------------------------- #
def test_walking_back_in_after_a_long_absence_earns_one_spoken_hello():
    rt, dev = _runtime(greet_after_s=300.0)
    rt.set_synthesizer(CountingSynth())
    _seed_absent(rt, dev, away_s=900.0)
    resp = _vision(rt, dev, FOUND)
    assert resp["result"] == "SUCCESS", resp
    text = resp["output"]["text"]
    assert "Sam" in text and len(text) < 70, text
    assert resp["output"]["markup"] and "<mark" in resp["output"]["markup"], \
        "the hello is performed, not read out flat"
    # ...and it was spoken: a CloudTTSResponse for the same event_id
    tts = rt.client.on(TTS_TOPIC.format(dev))
    assert tts and tts[0]["event_id"] == "evt-eye", tts


def test_the_hello_is_rate_limited_to_once_per_absence():
    rt, dev = _runtime()
    _seed_absent(rt, dev, away_s=900.0)
    first = _vision(rt, dev, FOUND, event_id="e1")
    assert first["result"] == "SUCCESS"
    # the tracker re-announces the same face: no second hello, no second turn
    state = dict(rt.robots[dev].extra["presence"])
    state.update({"face_present": False, "last_lost_at": state["greeted_at"] - 5.0})
    rt.robots[dev].extra["presence"] = state
    second = _vision(rt, dev, FOUND, event_id="e2")
    assert second["result"] == "NOREPLY_ACK", second


def test_a_short_step_out_of_frame_earns_nothing():
    rt, dev = _runtime(greet_after_s=300.0)
    _seed_absent(rt, dev, away_s=30.0)
    assert _vision(rt, dev, FOUND)["result"] == "NOREPLY_ACK"


def test_a_first_ever_sighting_never_greets():
    """`away_s` is None — Moxie does not shout hello at someone it has never seen."""
    rt, dev = _runtime(greet_after_s=1.0)
    assert _vision(rt, dev, FOUND)["result"] == "NOREPLY_ACK"


def test_the_greeting_can_be_switched_off_entirely():
    rt, dev = _runtime(greet_after_s=0.0)
    _seed_absent(rt, dev, away_s=9000.0)
    assert _vision(rt, dev, FOUND)["result"] == "NOREPLY_ACK"


def test_an_unpermitted_robot_is_never_greeted():
    rt, dev = _runtime(allow_unverified_bots=False)
    _seed_absent(rt, dev, away_s=9000.0)
    assert rt._greeting_for(dev, rt.robots[dev],
                            [{"name": "arrived", "away_s": 9000.0}]) is None


def test_bedtime_hours_suppress_the_hello():
    rt, dev = _runtime()
    _seed_absent(rt, dev, away_s=9000.0)
    now = time.time()
    import datetime
    cur = datetime.datetime.fromtimestamp(now)
    start = (cur - datetime.timedelta(minutes=30)).strftime("%H:%M")
    end = (cur + datetime.timedelta(minutes=30)).strftime("%H:%M")
    key = "weekend_bedtime" if cur.weekday() >= 5 else "weekday_bedtime"
    rt._config_overrides[dev] = {key: [start, end]}
    assert rt._in_bedtime(dev) is True
    assert _vision(rt, dev, FOUND)["result"] == "NOREPLY_ACK"


def test_outside_the_bedtime_window_the_hello_is_allowed():
    rt, dev = _runtime()
    _seed_absent(rt, dev, away_s=9000.0)
    import datetime
    cur = datetime.datetime.now()
    start = (cur + datetime.timedelta(hours=2)).strftime("%H:%M")
    end = (cur + datetime.timedelta(hours=4)).strftime("%H:%M")
    key = "weekend_bedtime" if cur.weekday() >= 5 else "weekday_bedtime"
    rt._config_overrides[dev] = {key: [start, end]}
    if rt._in_bedtime(dev):
        pytest.skip("the synthetic window wrapped onto now; nothing to assert")
    assert _vision(rt, dev, FOUND)["result"] == "SUCCESS"


def test_no_bedtime_configured_is_never_bedtime():
    rt, dev = _runtime()
    assert rt._in_bedtime(dev) is False
    rt._config_overrides[dev] = {"weekday_bedtime": None, "weekend_bedtime": None}
    assert rt._in_bedtime(dev) is False


def test_a_bedtime_window_that_wraps_midnight_is_understood():
    rt, dev = _runtime()
    import datetime
    for hhmm, inside in (("21:30", True), ("03:00", True), ("12:00", False)):
        at = datetime.datetime.now().replace(hour=int(hhmm[:2]), minute=int(hhmm[3:]),
                                             second=0, microsecond=0)
        key = "weekend_bedtime" if at.weekday() >= 5 else "weekday_bedtime"
        rt._config_overrides[dev] = {key: ["20:30", "07:00"]}
        assert rt._in_bedtime(dev, at.timestamp()) is inside, hhmm


# --------------------------------------------------------------------------- #
# 4. Never over a turn — the hello is queued instead
# --------------------------------------------------------------------------- #
def test_the_runtime_marks_a_robot_busy_for_the_whole_of_a_real_turn():
    seen = {}

    class Probe(MoxieApp):
        name = "probe"

        def respond(self, turn):
            seen["busy"] = turn.robot.device_id in rt._busy
            return Reply(text="ok")

    rt, dev = _runtime(Probe())
    drive_turn(rt, dev, "hi")
    assert seen["busy"] is True
    assert dev not in rt._busy, "and the marker is cleared when the turn ends"


def test_a_hello_earned_mid_turn_is_queued_not_spoken_over_the_answer():
    rt, dev = _runtime()
    _seed_absent(rt, dev, away_s=900.0)
    rt._busy.add(dev)                       # a turn is in flight
    resp = _vision(rt, dev, FOUND)
    assert resp["result"] == "NOREPLY_ACK", "never talk over Moxie's own answer"
    assert rt._pending_opener[dev], "the hello is kept for the next turn"


def test_a_queued_hello_is_delivered_as_chunk_zero_of_the_next_turn():
    rt, dev = _runtime()
    rt.set_synthesizer(CountingSynth())
    rt._pending_opener[dev] = "Hey Sam, there you are! I missed you."
    resp = drive_turn(rt, dev, "hello moxie")
    chats = rt.client.chat_replies(dev)
    assert len(chats) == 2, chats
    opener, answer = chats
    assert opener["output"]["text"] == "Hey Sam, there you are! I missed you."
    assert opener["result"] == "REPLY_PENDING" and opener["chunk_num"] == 0
    assert opener["consistency_control"] == {"is_completed": False}
    assert answer["result"] == "SUCCESS" and answer["chunk_num"] == 1
    assert answer["consistency_control"] == {"is_completed": True}
    assert resp is answer or resp == answer
    assert dev not in rt._pending_opener, "delivered once, then gone"


def test_a_queued_hello_is_delivered_ahead_of_a_streamed_answer_too():
    from moxie_sdk.types import ReplyChunk

    class StreamApp(MoxieApp):
        name = "stream"

        def respond(self, turn):
            return Reply(text="fallback")

        def respond_stream(self, turn):
            yield ReplyChunk(text="First sentence here.")
            yield ReplyChunk(text="And the last one.", final=True)

    rt, dev = _runtime(StreamApp())
    rt._pending_opener[dev] = "Oh hello Sam! It is so good to see you again."
    drive_turn(rt, dev, "hi")
    chats = rt.client.chat_replies(dev)
    assert chats[0]["output"]["text"].startswith("Oh hello Sam")
    assert chats[0]["chunk_num"] == 0
    assert [c["chunk_num"] for c in chats] == [0, 1, 2], chats
    assert chats[-1]["result"] == "SUCCESS"


# --------------------------------------------------------------------------- #
# 5. Presence reaches the brain's prompt
# --------------------------------------------------------------------------- #
def test_the_turn_carries_a_presence_snapshot():
    app = EchoApp()
    rt, dev = _runtime(app)
    _seed_absent(rt, dev, away_s=900.0)
    _vision(rt, dev, FOUND, event_id="eye")
    _fresh_pool(rt)
    drive_turn(rt, dev, "hi moxie", event_id="talk")
    turn = app.turns[-1]
    assert turn.presence["face_present"] is True
    assert turn.presence["known"] is True
    assert turn.presence["line"], "an arrival after 15 minutes is worth telling the brain"


def test_a_robot_that_has_never_seen_anyone_carries_an_empty_line():
    app = EchoApp()
    rt, dev = _runtime(app)
    drive_turn(rt, dev, "hi moxie")
    assert app.turns[-1].presence["known"] is False
    assert app.turns[-1].presence["line"] == ""


def test_the_llm_system_prompt_gains_the_presence_line_only_when_it_matters():
    from moxie_sdk.apps.llm_app import LLMApp
    from moxie_sdk.types import ChildProfile, RobotContext, Turn
    app = LLMApp("http://local", "k", client=object())
    robot = RobotContext(device_id="d_1", child=ChildProfile(nickname="Sam"))
    quiet = Turn(robot=robot, speech="hi", presence={"line": ""})
    assert "What you can see right now" not in app._system(robot, quiet)
    loud = Turn(robot=robot, speech="hi",
                presence={"line": "A child has just come into view in front of you."})
    system = app._system(robot, loud)
    assert "What you can see right now: A child has just come into view" in system


def test_a_content_module_prompt_can_read_presence():
    # This prompt uses a Jinja `{% if %}` block, which only the full renderer
    # understands — the minimal fallback leaves it literal. jinja2 is an OPTIONAL
    # extra (`pyproject.toml`:25 `content`), and the shipped container ships without
    # it, so the dependency has to be declared or this test lies about that shape.
    pytest.importorskip("jinja2", reason="the `{% if %}` form needs the full renderer")
    from moxie_sdk.content.render import render_prompt
    from moxie_sdk.content.content_app import _presence_vars
    from moxie_sdk.types import ChildProfile, RobotContext
    robot = RobotContext(device_id="d_1", child=ChildProfile(nickname="Sam"))
    robot.extra["presence"] = P.new_state()
    robot.extra["presence"].update({"face_present": True, "present_since": time.time()})
    out = render_prompt("{% if presence.face_present %}They are here.{% endif %}",
                        {"presence": _presence_vars(robot)})
    assert out == "They are here."
