"""
Presence in the runtime — the robot's own eyes reaching the turn loop.

A subscribed perception event arrives as the `speech` of an ordinary `RemoteChatRequest`
(vision.md §1.1; OpenMoxie `doc/RemoteModuleAPI.md`, MIT), so these tests drive real
`events/remote-chat` payloads through `MoxieRuntime` over a fake transport.

Hermetic: no sleeps, broker or model. Elapsed time is expressed by seeding the presence
record, never by waiting.
"""
import time

import pytest

from helpers_runtime import fresh_pool, seed_absent  # noqa: E402
from helpers_runtime import (CountingSynth, drive_turn,   # noqa: E402
                             make_runtime)

from moxie_sdk import presence as P                                    # noqa: E402
from moxie_sdk.app import MoxieApp                                     # noqa: E402
from moxie_sdk.types import Reply, ResultCode                          # noqa: E402

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


def _vision(rt, dev, name, *, event_id="evt-eye", input_vars=None):
    """Publish one vision event as the robot does, and return the response."""
    return drive_turn(rt, dev, name, event_id=event_id,
                      **({"input_vars": input_vars} if input_vars else {}))


# --------------------------------------------------------------------------- #
# 1. Ingest — the events land on RobotContext, and never on the brain
# --------------------------------------------------------------------------- #
def test_a_vision_event_updates_presence_and_never_reaches_the_brain():
    """The contract requires *some* response, so the answer is `NOREPLY_ACK` (ResultCode 6,
    "acknowledged, no spoken line") — and the event is not something a child said, so it
    never reaches `respond` or the history; the app's event hook does see it."""
    app = EchoApp()
    rt, dev = _runtime(app)
    resp = _vision(rt, dev, FOUND)
    assert app.turns == [] and rt.history.get(dev, []) == []
    assert app.events and app.events[0][0] == FOUND
    assert resp["command"] == "remote_chat" and resp["result"] == ResultCode.NOREPLY_ACK, resp
    assert resp["event_id"] == "evt-eye"
    assert (resp["output"]["text"] or "") == ""
    state = rt.robots[dev].extra["presence"]
    assert state["face_present"] is True and state["faces_seen"] == 1


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
    fresh_pool(rt)
    second = drive_turn(rt, dev, "again", event_id="e2")
    # only the action-less entry every reply carries (OpenMoxie's envelope), no subscription
    assert second["response_actions"] == [{"output_type": "GLOBAL_RESPONSE"}], second
    assert not _subscribed(second), second


# --------------------------------------------------------------------------- #
# 2b. …and the latch that says "once" must stop saying it when it stops being true
# --------------------------------------------------------------------------- #
#
# The robot drops subscriptions when a module exits (RemoteModuleAPI §Unsubscribing), so
# `_vision_subscribed[device] = module` must be cleared on exit, wake and outage — else we
# never re-send `EventSubscription.active[]` and vision/QR events silently go nowhere
# (cf. owner "crossed ears" reports, upstream openmoxie PR #59). Shares the roster fix
# (`_forget_robot_state`): forgetting costs one redundant message, remembering costs eyes.
#
# Ceiling: no physical robot has sent this appliance a vision event. These tests prove we
# RE-SUBSCRIBE, not that a robot then delivers.

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
    fresh_pool(rt)

    rt.client.drop()
    rt.client.up()
    fresh_pool(rt)
    assert _subscribed(drive_turn(rt, dev, "again", event_id="e2")), \
        "after an outage the robot has no subscription and we never re-sent one"


def test_a_module_exit_makes_the_next_reply_re_subscribe():
    """The contract's own sentence, as a test. The latch is keyed `(device, module)`,
    which catches a switch A→B but **not** a re-entry A→B→A — the key matches again and
    the subscription is never re-sent, even though the robot dropped it on the exit."""
    rt, dev = _runtime()
    module = rt.robots[dev].module_id
    assert _subscribed(drive_turn(rt, dev, "hello", event_id="e1"))
    fresh_pool(rt)

    rt._end_conversation(dev, "module exit")        # A exits
    rt._pool.shutdown(wait=True)
    fresh_pool(rt)
    assert rt.robots[dev].module_id == module, "the test needs the SAME module re-entered"
    assert _subscribed(drive_turn(rt, dev, "again", event_id="e2")), \
        "the module exited and dropped the subscription; we never re-sent it"


def test_waking_a_robot_makes_the_next_reply_re_subscribe():
    """Upstream openmoxie PR #59's case. A robot that has been asleep has dropped its
    subscriptions, so a wake is one of the moments our latch stops being true."""
    rt, dev = _runtime()
    assert _subscribed(drive_turn(rt, dev, "hello", event_id="e1"))
    fresh_pool(rt)

    out = rt.wake_robot(dev)
    assert out["published"] is True, out
    fresh_pool(rt)
    assert _subscribed(drive_turn(rt, dev, "again", event_id="e2")), \
        "the robot was woken with no subscription and we never re-sent one"


def test_a_robot_the_broker_says_left_forgets_everything_we_believed_about_it():
    """`_device_disconnect` (real evidence the client left) drops BOTH caches. The vision
    latch is also cleared via `_end_conversation`, so the uniquely load-bearing half here is
    `_seen_since_connect` — without it a departed robot is never re-onboarded (mutation V4).
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
    assert resp["response_actions"] == [{"output_type": "GLOBAL_RESPONSE"}], resp
    assert not _subscribed(resp), resp


# --------------------------------------------------------------------------- #
# 3. The greeting — the delight, and every gate on it
# --------------------------------------------------------------------------- #
def test_walking_back_in_after_a_long_absence_earns_one_spoken_hello():
    rt, dev = _runtime(greet_after_s=300.0)
    rt.set_synthesizer(CountingSynth())
    seed_absent(rt, dev, away_s=900.0)
    resp = _vision(rt, dev, FOUND)
    assert resp["result"] == ResultCode.SUCCESS, resp
    text = resp["output"]["text"]
    assert "Sam" in text and len(text) < 70, text
    assert resp["output"]["markup"] and "<mark" in resp["output"]["markup"], \
        "the hello is performed, not read out flat"
    # ...and it was spoken: a CloudTTSResponse for the same event_id
    tts = rt.client.on(TTS_TOPIC.format(dev))
    assert tts and tts[0]["event_id"] == "evt-eye", tts


def test_the_hello_is_rate_limited_to_once_per_absence():
    rt, dev = _runtime()
    seed_absent(rt, dev, away_s=900.0)
    first = _vision(rt, dev, FOUND, event_id="e1")
    assert first["result"] == ResultCode.SUCCESS
    # the tracker re-announces the same face: no second hello, no second turn
    state = dict(rt.robots[dev].extra["presence"])
    state.update({"face_present": False, "last_lost_at": state["greeted_at"] - 5.0})
    rt.robots[dev].extra["presence"] = state
    second = _vision(rt, dev, FOUND, event_id="e2")
    assert second["result"] == ResultCode.NOREPLY_ACK, second


def test_a_short_step_out_of_frame_earns_nothing():
    rt, dev = _runtime(greet_after_s=300.0)
    seed_absent(rt, dev, away_s=30.0)
    assert _vision(rt, dev, FOUND)["result"] == ResultCode.NOREPLY_ACK


def test_a_first_ever_sighting_never_greets():
    """`away_s` is None — Moxie does not shout hello at someone it has never seen."""
    rt, dev = _runtime(greet_after_s=1.0)
    assert _vision(rt, dev, FOUND)["result"] == ResultCode.NOREPLY_ACK


def test_the_greeting_can_be_switched_off_entirely():
    rt, dev = _runtime(greet_after_s=0.0)
    seed_absent(rt, dev, away_s=9000.0)
    assert _vision(rt, dev, FOUND)["result"] == ResultCode.NOREPLY_ACK


def test_an_unpermitted_robot_is_never_greeted():
    rt, dev = _runtime(allow_unverified_bots=False)
    seed_absent(rt, dev, away_s=9000.0)
    assert rt._greeting_for(dev, rt.robots[dev],
                            [{"name": "arrived", "away_s": 9000.0}]) is None


def test_bedtime_hours_suppress_the_hello():
    """Clock-RELATIVE on purpose: `rt._in_bedtime` reads the real clock itself, so pinning
    ours would test a different function. now±30 min contains now at all 1440 minutes,
    wrap included (premise test below). Both weekday keys are written, so a midnight
    between our read and the runtime's cannot pick the wrong one. (Outside a window the
    hello is allowed: the arrival test above has no window at all.)"""
    rt, dev = _runtime()
    seed_absent(rt, dev, away_s=9000.0)
    import datetime
    cur = datetime.datetime.now()
    start = (cur - datetime.timedelta(minutes=30)).strftime("%H:%M")
    end = (cur + datetime.timedelta(minutes=30)).strftime("%H:%M")
    rt._config_overrides[dev] = {"weekday_bedtime": [start, end],
                                 "weekend_bedtime": [start, end]}
    assert rt._in_bedtime(dev) is True, f"window {start}-{end} must contain {cur:%H:%M}"
    assert _vision(rt, dev, FOUND)["result"] == ResultCode.NOREPLY_ACK


def test_the_synthetic_bedtime_windows_hold_at_every_minute():
    """The premise of the clock-relative bedtime test, checked with no wall clock over all
    1440 minutes against the same `in_bedtime` helper the runtime calls — so a wrap bug
    fails here deterministically, not once a day above (plus a window that excludes now)."""
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
    """Clock-independent: only today's DATE is borrowed (hour/minute overwritten) and the
    timestamp is passed explicitly, so 20:30-07:00 gives the same answers for 21:30 / 03:00
    / 12:00 on any date. A real date is kept so a DST/timezone regression would surface."""
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
    seed_absent(rt, dev, away_s=900.0)
    rt._busy.add(dev)                       # a turn is in flight
    resp = _vision(rt, dev, FOUND)
    assert resp["result"] == ResultCode.NOREPLY_ACK, "never talk over Moxie's own answer"
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
    assert opener["result"] == ResultCode.REPLY_PENDING and opener["chunk_num"] == 0
    assert opener["consistency_control"] == {"is_completed": False}
    assert answer["result"] == ResultCode.SUCCESS and answer["chunk_num"] == 1
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
    assert chats[-1]["result"] == ResultCode.SUCCESS


# --------------------------------------------------------------------------- #
# 5. Presence reaches the brain's prompt
# --------------------------------------------------------------------------- #
def test_the_turn_carries_a_presence_snapshot():
    app = EchoApp()
    rt, dev = _runtime(app)
    seed_absent(rt, dev, away_s=900.0)
    _vision(rt, dev, FOUND, event_id="eye")
    fresh_pool(rt)
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
    # This prompt uses a Jinja `{% if %}` block. jinja2 is an optional SDK extra
    # (`content`), so a bare `pip install moxie-cloud-sdk` uses the fallback, which strips
    # the block — hence the importorskip. The container ships jinja2
    # (`test_render_container_deps.py`); see `test_render_fallback.py`.
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


def test_no_presence_lock_block_calls_something_that_retakes_it():
    """`_presence_lock` is NOT reentrant, and `_forget_robot_state` (reached from the paho
    thread, `_end_conversation`, `wake_robot`, `_device_disconnect`) acquires it. Calling one
    of those inside a `with self._presence_lock:` block would silently deadlock the MQTT
    loop, so it is checked structurally over the runtime's source.
    """
    from helpers_runtime import runtime_source
    src = runtime_source().split("\n")
    risky = ("_forget_robot_state", "_end_conversation", "_device_disconnect",
             "wake_robot", "_on_disconnect", "_vision_subscription")
    blocks = 0
    for i, line in enumerate(src):
        if "with self._presence_lock:" not in line:
            continue
        blocks += 1
        indent = len(line) - len(line.lstrip())
        j = i + 1
        while j < len(src):
            cur = src[j]
            if cur.strip() and (len(cur) - len(cur.lstrip())) <= indent:
                break
            stripped = cur.strip()
            # A comment mentioning one of these is documentation, not a call (playbook
            # rule 17: a guard must assert over code, not over the whole file).
            if not stripped.startswith("#"):
                for name in risky:
                    assert f"{name}(" not in stripped, (
                        f"moxie_runtime line {j + 1} calls {name}() while holding "
                        "the non-reentrant _presence_lock — this self-deadlocks the MQTT "
                        f"loop:\n    {stripped}")
            j += 1
    assert blocks >= 5, f"only {blocks} presence-lock blocks found — has the lock moved?"
