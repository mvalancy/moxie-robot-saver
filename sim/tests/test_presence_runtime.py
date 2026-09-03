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
    """Put this robot's presence where it would be `away_s` seconds after a departure.

    Clock-relative and correct: presence is scored as an AGE (`greet_after_s`), so the
    state is defined by offsets from now and means the same thing at any hour. A pinned
    epoch would make every robot look absent for years."""
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


# --------------------------------------------------------------------------- #
# 2b. …and the latch that says "once" must stop saying it when it stops being true
# --------------------------------------------------------------------------- #
#
# `_vision_subscribed[device] = module` was set and **never cleared** — not on a module
# exit, not on a wake, not on a broker outage. Meanwhile the recovered contract says
# *"events are automatically unsubscribed when the module exits"* (RemoteModuleAPI
# §Unsubscribing), which the latch's own docstring quoted. So the robot dropped the
# subscription while our latch still claimed we held it, and we never re-sent
# `EventSubscription.active[]`: vision and QR events went nowhere, silently, with nothing
# logged on either side.
#
# Evidence this is real and not just readable-from-the-code: four independent owner
# reports of "crossed ears", and upstream openmoxie PR #59 diagnoses the sleep/wake
# variant as exactly this (the STT subscribe must be re-sent on wake).
#
# **The ceiling, stated where it cannot be missed:** no physical robot has ever sent this
# appliance a vision event. These tests prove *we re-subscribe*. They cannot prove a robot
# then delivers, and a green run here must never be read as saying it does.
#
# This is the same defect as the roster ghost, and deliberately shares its fix
# (`_forget_robot_state`): a cached belief about the robot's state outliving the robot's
# actual state. Both caches are pure optimisation — being wrong by forgetting costs one
# redundant message; being wrong by remembering costs eyes that never report.

def _subscribed(resp) -> bool:
    """Did this reply carry an `EventSubscription.active[]`?"""
    for action in (resp.get("response_actions") or []):
        if action.get("event_subscription", {}).get("active"):
            return True
    return False


def test_a_broker_outage_makes_the_next_reply_re_subscribe():
    """The robot's session went with the broker; our latch must not outlive it."""
    rt, dev = _runtime()
    assert _subscribed(drive_turn(rt, dev, "hello", event_id="e1"))
    _fresh_pool(rt)

    rt.client.drop()
    rt.client.up()
    _fresh_pool(rt)
    assert _subscribed(drive_turn(rt, dev, "again", event_id="e2")), \
        "after an outage the robot has no subscription and we never re-sent one"


def test_a_module_exit_makes_the_next_reply_re_subscribe():
    """The contract's own sentence, as a test. The latch is keyed `(device, module)`,
    which catches a switch A→B but **not** a re-entry A→B→A — the key matches again and
    the subscription is never re-sent, even though the robot dropped it on the exit."""
    rt, dev = _runtime()
    module = rt.robots[dev].module_id
    assert _subscribed(drive_turn(rt, dev, "hello", event_id="e1"))
    _fresh_pool(rt)

    rt._end_conversation(dev, "module exit")        # A exits
    rt._pool.shutdown(wait=True)
    _fresh_pool(rt)
    assert rt.robots[dev].module_id == module, "the test needs the SAME module re-entered"
    assert _subscribed(drive_turn(rt, dev, "again", event_id="e2")), \
        "the module exited and dropped the subscription; we never re-sent it"


def test_waking_a_robot_makes_the_next_reply_re_subscribe():
    """Upstream openmoxie PR #59's case. A robot that has been asleep has dropped its
    subscriptions, so a wake is one of the moments our latch stops being true."""
    rt, dev = _runtime()
    assert _subscribed(drive_turn(rt, dev, "hello", event_id="e1"))
    _fresh_pool(rt)

    out = rt.wake_robot(dev)
    assert out["published"] is True, out
    _fresh_pool(rt)
    assert _subscribed(drive_turn(rt, dev, "again", event_id="e2")), \
        "the robot was woken with no subscription and we never re-sent one"


def test_a_robot_the_broker_says_left_forgets_everything_we_believed_about_it():
    """`_device_disconnect` is the one place with *real evidence about the robot* — the
    broker told us the client went away — so it drops both caches, not just one.

    Asserting both matters: the vision half is **also** covered by `_end_conversation`,
    which `_device_disconnect` calls, so a test that checked only the latch passed with
    this line deleted (found by the mutation checker, V4). The half that is uniquely
    load-bearing here is `_seen_since_connect` — without it a robot that genuinely left
    stays 'confirmed' forever and is never re-onboarded when it returns.
    """
    rt, dev = _runtime()
    drive_turn(rt, dev, "hello", event_id="e1")
    rt._seen_since_connect.add(dev)
    assert rt._vision_subscribed.get(dev) is not None

    rt._device_disconnect(dev)
    assert dev not in rt._vision_subscribed
    assert dev not in rt._seen_since_connect


def test_forgetting_the_subscription_does_not_forget_the_conversation():
    """The other direction, and the line between belief and data. Everything cleared here
    is *our model of the robot's state*; `history`, presence and the `RobotContext` are
    the robot's own data and must survive — a child mid-conversation when the broker
    blinked continues it rather than meeting a stranger."""
    rt, dev = _runtime()
    drive_turn(rt, dev, "hello", event_id="e1")
    rt.robots[dev].extra["presence"] = {"face_present": True, "faces_seen": 3}
    before_ctx = rt.robots[dev]
    before_history = list(rt.history[dev])
    assert before_history, "the test needs a conversation to preserve"

    rt.client.drop()
    rt.client.up()

    assert rt.robots[dev] is before_ctx
    assert rt.history[dev] == before_history
    assert rt.robots[dev].extra["presence"]["faces_seen"] == 3


def test_the_two_caches_are_invalidated_by_one_rule():
    """The generalisation, pinned. Both defects were a cached belief about the robot
    outliving the robot's state, and a single broken connection must clear both — two
    independent patches would drift apart at the next one."""
    rt, dev = _runtime()
    drive_turn(rt, dev, "hello", event_id="e1")
    rt._seen_since_connect.add(dev)
    assert rt._vision_subscribed and rt._seen_since_connect

    rt.client.drop()
    assert not rt._vision_subscribed, "the vision latch survived the outage"
    assert not rt._seen_since_connect, "the onboarding latch survived the outage"


def test_a_module_exit_does_not_claim_the_robot_went_away():
    """…and the lifetimes really are different, which is why one method takes a flag
    rather than two methods existing. A module exiting says nothing about whether the
    robot is connected, so it must NOT force a re-onboard and a fresh `app.on_connect`."""
    rt, dev = _runtime()
    rt._seen_since_connect.add(dev)
    rt._end_conversation(dev, "module exit")
    assert dev in rt._seen_since_connect, "a module exit un-onboarded the robot"
    assert dev not in rt._vision_subscribed


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
    """Clock-RELATIVE on purpose: the subject is `rt._in_bedtime`, which reads the real
    `datetime.now()` itself (moxie_runtime.py:1723), so pinning the test's clock would
    only test a different function. A window of now±30 min contains now at every one of
    the 1440 minutes of a day, wrap included — verified exhaustively against
    `cloud_config.in_bedtime`, whose `start > end` branch is what makes the wrap work.

    **Both** keys are written, never just the one today's weekday picks: the runtime
    re-reads the clock a moment after this test does, and on a Fri→Sat / Sun→Mon midnight
    those two reads disagree about which key to look at. Writing both makes the weekday
    irrelevant instead of nearly-always-right. (Same move as the PR #63 telehealth fix.)"""
    rt, dev = _runtime()
    _seed_absent(rt, dev, away_s=9000.0)
    import datetime
    cur = datetime.datetime.now()
    start = (cur - datetime.timedelta(minutes=30)).strftime("%H:%M")
    end = (cur + datetime.timedelta(minutes=30)).strftime("%H:%M")
    rt._config_overrides[dev] = {"weekday_bedtime": [start, end],
                                 "weekend_bedtime": [start, end]}
    assert rt._in_bedtime(dev) is True, f"window {start}-{end} must contain {cur:%H:%M}"
    assert _vision(rt, dev, FOUND)["result"] == "NOREPLY_ACK"


def test_outside_the_bedtime_window_the_hello_is_allowed():
    """The other side of the same clock-relative gate, and for the same reason.

    A window of now+2 h … now+4 h excludes now at every one of the 1440 minutes of a day
    — including the hours where it wraps midnight, because it then reads `start < end`
    over a wrapped pair rather than as a wrapping window. That was verified exhaustively,
    which is why the `pytest.skip("the synthetic window wrapped onto now")` this test used
    to carry is gone: it could never fire, and a skip that cannot fire is an escape hatch
    a future regression would slip through silently. Both keys, as above."""
    rt, dev = _runtime()
    _seed_absent(rt, dev, away_s=9000.0)
    import datetime
    cur = datetime.datetime.now()
    start = (cur + datetime.timedelta(hours=2)).strftime("%H:%M")
    end = (cur + datetime.timedelta(hours=4)).strftime("%H:%M")
    rt._config_overrides[dev] = {"weekday_bedtime": [start, end],
                                 "weekend_bedtime": [start, end]}
    assert rt._in_bedtime(dev) is False, f"window {start}-{end} must exclude {cur:%H:%M}"
    assert _vision(rt, dev, FOUND)["result"] == "SUCCESS"


def test_the_synthetic_windows_the_two_tests_above_build_hold_at_every_minute():
    """The premise the two clock-relative tests above rest on, asserted rather than
    claimed — with no wall clock at all, over all 1440 minutes of a day.

    Those tests cannot pin their own clock (the runtime reads it), so their correctness
    depends on a property of the *window they synthesize*: now±30 min always contains
    now, and now+2 h…+4 h never does. That property is exactly the kind of thing that
    reads as obvious and is not — `["00:00", "23:59"]` also read as "all day" and was
    false for one minute a night (PR #63). Checked here against the same pure helper the
    runtime calls, so if a future change to `in_bedtime`'s wrap handling breaks the
    premise, this fails deterministically instead of the pair above going red once a day."""
    import datetime
    from moxie_sdk.cloud_config import in_bedtime
    base = datetime.datetime(2026, 9, 2)                      # any day; only H:M matters
    for minute in range(1440):
        cur = base + datetime.timedelta(minutes=minute)
        near = [(cur - datetime.timedelta(minutes=30)).strftime("%H:%M"),
                (cur + datetime.timedelta(minutes=30)).strftime("%H:%M")]
        far = [(cur + datetime.timedelta(hours=2)).strftime("%H:%M"),
               (cur + datetime.timedelta(hours=4)).strftime("%H:%M")]
        assert in_bedtime({"weekday_bedtime": near, "weekend_bedtime": near}, cur) is True, \
            f"{near} must contain {cur:%H:%M}"
        assert in_bedtime({"weekday_bedtime": far, "weekend_bedtime": far}, cur) is False, \
            f"{far} must exclude {cur:%H:%M}"


def test_no_bedtime_configured_is_never_bedtime():
    rt, dev = _runtime()
    assert rt._in_bedtime(dev) is False
    rt._config_overrides[dev] = {"weekday_bedtime": None, "weekend_bedtime": None}
    assert rt._in_bedtime(dev) is False


def test_a_bedtime_window_that_wraps_midnight_is_understood():
    """Clock-INDEPENDENT despite the `datetime.now()`: only today's *date* is borrowed,
    the hour and minute are overwritten, and the fixed 20:30-07:00 window's answer for
    21:30 / 03:00 / 12:00 is the same on every date. The timestamp is passed to
    `_in_bedtime` explicitly, so the runtime does not read its own clock here either, and
    the weekday the key is chosen by is `at`'s — the same one the runtime will resolve.
    Leave it reading `now()`: pinning a date would test nothing extra and would hide a
    real DST/timezone regression that a real date would surface."""
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
    # This prompt uses a Jinja `{% if %}` block. jinja2 is still an OPTIONAL extra of the
    # SDK (`pyproject.toml` `content`), so a bare `pip install moxie-cloud-sdk` reaches
    # the dependency-free fallback and this exact assertion would not hold there — hence
    # the importorskip. What is NO LONGER true is the reason this comment used to give
    # ("the shipped container ships without it"): PR #62 added `jinja2>=3.0` to
    # `mqtt/requirements.txt`, so the container runs the real renderer on purpose, and
    # `test_render_container_deps.py` pins that split in both directions. Since PR #62's
    # second half the fallback *strips* a block it cannot evaluate rather than leaking
    # the template source into a system prompt, so the two paths differ in what they
    # render, never in whether they leak — see `test_render_fallback.py`.
    pytest.importorskip("jinja2", reason="the `{% if %}` form needs the full renderer")
    from moxie_sdk.content.render import render_prompt
    from moxie_sdk.content.content_app import _presence_vars
    from moxie_sdk.types import ChildProfile, RobotContext
    robot = RobotContext(device_id="d_1", child=ChildProfile(nickname="Sam"))
    robot.extra["presence"] = P.new_state()
    # `present_since` is an age the renderer may phrase; now keeps it fresh, and the
    # assertion below does not read it — hour-independent.
    robot.extra["presence"].update({"face_present": True, "present_since": time.time()})
    out = render_prompt("{% if presence.face_present %}They are here.{% endif %}",
                        {"presence": _presence_vars(robot)})
    assert out == "They are here."
