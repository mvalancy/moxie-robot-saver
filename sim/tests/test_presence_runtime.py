"""
Presence in the runtime — the robot's own eyes reaching the turn loop.

A subscribed perception event arrives as the `speech` of an ordinary `RemoteChatRequest`
(vision.md §1.1; OpenMoxie `doc/RemoteModuleAPI.md`, MIT), so these tests drive real
`events/remote-chat` payloads through `MoxieRuntime` over a fake transport.

Hermetic: no sleeps, broker or model. Elapsed time is expressed by seeding the presence
record or by moving presence.py's own clock by hand (`_eye_clock`), never by waiting.
"""
import json
import re
import time
import types

import pytest

from helpers_runtime import fresh_pool, seed_absent  # noqa: E402
from helpers_runtime import (CountingSynth, drive_turn,   # noqa: E402
                             make_runtime)

from moxie_sdk import presence as P                                    # noqa: E402
from moxie_sdk import safety as S                                      # noqa: E402
from moxie_sdk import vocab                                            # noqa: E402
from moxie_sdk.app import MoxieApp                                     # noqa: E402
from moxie_sdk.cloud_config import CHILD_MASK                          # noqa: E402
from moxie_sdk.store import JsonStore                                  # noqa: E402
from moxie_sdk.types import Action, ActionType, Reply, ResultCode      # noqa: E402

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
    between our read and the runtime's cannot pick the wrong one. Now is read on the
    house's clock (`house_zone`), the one bedtime is judged on, never this process's.
    (Outside a window the hello is allowed: the arrival test above has no window at all.)"""
    rt, dev = _runtime()
    seed_absent(rt, dev, away_s=9000.0)
    import datetime
    cur = datetime.datetime.now(rt.house_zone(dev).tz)
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
    """Clock-independent: only the house's today's DATE is borrowed (hour/minute
    overwritten, on the house's clock, which bedtime is judged on) and the timestamp is
    passed explicitly, so 20:30-07:00 gives the same answers for 21:30 / 03:00 / 12:00 on
    any date. A real date is kept so a DST/timezone regression would surface."""
    rt, dev = _runtime()
    import datetime
    for hhmm, inside in (("21:30", True), ("03:00", True), ("12:00", False)):
        at = datetime.datetime.now(rt.house_zone(dev).tz).replace(
            hour=int(hhmm[:2]), minute=int(hhmm[3:]), second=0, microsecond=0)
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



# --------------------------------------------------------------------------- #
# 6. A pack's answer to the robot's eye passes the output check
# --------------------------------------------------------------------------- #
# The plain turn, each streamed chunk and the hello are checked on Moxie's side before they
# are staged, and so is the line a pack answers a subscribed event with
# (`_wake_subscribed_pack`). The child did not prompt that line, so one the check stops is
# not swapped for a redirect: the event is answered NOREPLY_ACK and the line's act,
# end_turn and subscribe go with it. A flagged line is said. Either verdict reaches the
# parent's review queue once per presence episode, with no child's name in it.

#: Blocked on Moxie's side and only flagged on the child's: Moxie never asks a child for an
#: address (personal_info).
ASKS_ADDRESS = "Found you! Now tell me your home address."
#: Flagged on Moxie's side (a word on violence_talk's list) and harmless.
PUNCH_THE_AIR = "Found you! Let's punch the air, hooray!"
#: Matches nothing in the table.
BIG_SMILE = "Found you! I spy a big smile."
#: What the pack does along with its line: re-arm the QR scanner.
ARM_QR = Action(type=ActionType.EXECUTE, function="eb_enable_qr", args=["true"])
#: The pack and the item the robot is in (`make_runtime`: FREE_CHAT/default), as the feed
#: and the log name them.
SOURCE = "eye-pack/FREE_CHAT/default"


class _EyePack(MoxieApp):
    """Asks for `events` on an ordinary turn, then answers each with a scripted `Reply`.
    By default it carries every field the runtime reads from a woken app: a line, the
    pack's markup for it or none, an act, a renewed `subscribe` and `end_turn`
    (`ContentApp.perceive` returns this shape for a matched `turn.before` rule).
    `line_only` is the commonest eye rule: a line and nothing else."""
    name = "eye-pack"

    def __init__(self, text, markup=None, *, line_only=False, events=(FOUND,)):
        self.text, self.markup, self.line_only = text, markup, line_only
        self.events = list(events)
        self.perceived = []

    def respond(self, turn):
        return Reply(text="ok", subscribe=list(self.events))

    def perceive(self, turn):
        self.perceived.append(turn.speech)
        if self.line_only:
            return Reply(text=self.text)
        return Reply(text=self.text, markup=self.markup, actions=[ARM_QR],
                     subscribe=[P.QR_EVENT], end_turn=True)


def _armed(tmp_path, pack):
    """A runtime whose pack asked for its events on one ordinary turn: `(rt, dev, synth)`."""
    rt, dev = _runtime(pack, store=JsonStore(str(tmp_path)))
    synth = CountingSynth()
    rt.set_synthesizer(synth)
    drive_turn(rt, dev, "hello", event_id="e-arm")
    fresh_pool(rt)
    return rt, dev, synth


def _eye_line(tmp_path, text, markup=None, *, line_only=False, capsys=None):
    """The pack asks for faces; then the child walks back in after a long absence, with a
    hello due, and the pack answers that `eb-found-face` with `text`. Returns what that one
    event made: `resp` (the wire answer), `said` (what the robot was given to speak),
    `handed` (what `_publish_chat` was given: `end_turn` has no wire field), `feed` (the
    feed lines it wrote) and `out` (what it logged, with `capsys`)."""
    rt, dev, synth = _armed(tmp_path, _EyePack(text, markup, line_only=line_only))
    seed_absent(rt, dev, away_s=9000.0)
    spoke, noted = len(synth.spoken), len(rt.recent)
    if capsys is not None:
        capsys.readouterr()
    handed = []
    publish = rt._publish_chat

    def recording(*args, **kw):
        handed.append(kw)
        return publish(*args, **kw)

    rt._publish_chat = recording
    resp = _vision(rt, dev, FOUND, event_id="e-eye")
    assert rt.app.perceived == [FOUND], "sanity: the face woke the pack that asked for it"
    replies = [r for r in rt.client.chat_replies(dev) if r.get("event_id") == "e-eye"]
    assert len(replies) == 1 and len(handed) == 1, "one event, one answer"
    return types.SimpleNamespace(
        rt=rt, dev=dev, resp=resp, said=synth.spoken[spoke:], handed=handed[0],
        feed=[r["text"] for r in list(rt.recent)[noted:]],
        out=capsys.readouterr().out if capsys is not None else "")


@pytest.mark.parametrize("line_only", [False, True],
                         ids=["line-markup-act-subscribe-end_turn", "line-only"])
def test_a_blocked_line_to_the_robots_eye_says_nothing(tmp_path, capsys, line_only):
    """A line the table stops on Moxie's side, and the child never asked for it: nothing is
    said in its place, neither a redirect nor the hello that was due (`NOREPLY_ACK`, as
    when no presence line is due), and the act, the renewed subscription and end_turn go
    with it. The parent gets the block in the review queue, and the feed and the log name
    the pack and module, never the line. A line and nothing else is the commonest eye
    rule, so the check must not lean on the pack also acting or subscribing."""
    assert S.assess(ASKS_ADDRESS, role=S.MOXIE).action == S.BLOCK, "premise: Moxie's side"
    assert S.assess(ASKS_ADDRESS, role=S.CHILD).action == S.FLAG, \
        "premise: a child's side check would let it through"
    eye = _eye_line(tmp_path, ASKS_ADDRESS, markup=vocab.mood_mark(2) + ASKS_ADDRESS,
                    line_only=line_only, capsys=capsys)
    resp = eye.resp

    assert resp["result"] == ResultCode.NOREPLY_ACK and resp["event_id"] == "e-eye", resp
    assert not resp["output"].get("text") and not resp["output"].get("markup"), resp
    assert eye.said == [], "something was said for a line the child never asked for"
    assert "address" not in json.dumps(resp), "the blocked line reached the robot"
    assert all(set(a) == {"output_type"} for a in resp.get("response_actions") or []), \
        "the blocked line's act or subscription went out without it"
    assert (eye.handed.get("actions"), eye.handed.get("subscribe"),
            eye.handed.get("end_turn", False)) == (None, None, False), eye.handed
    assert P.QR_EVENT not in eye.rt._pack_subscribed.get(eye.dev, {}), \
        "a pack whose words are blocked earns no wake from that line"

    view = eye.rt.safety_view(eye.dev)
    assert view["unreviewed"] == 1, view
    row = view["events"][0]
    assert (row["side"], row["action"], row["categories"], row["phrase_id"]) == \
        ("moxie", "block", ["personal_info"], None), row
    assert row["excerpt"] and "address" not in row["excerpt"], row
    assert "count" not in row, "one event is one row, with no count on it"

    assert not any("address" in line for line in eye.feed), eye.feed
    assert (f"🧬 a content pack ({SOURCE}) answered {FOUND} with a line the safety check "
            f"stopped: Moxie said nothing") in eye.feed, eye.feed
    assert "address" not in eye.out, "the log printed the blocked line"
    assert (f"[runtime] 🧬 {eye.dev}: {FOUND} woke a content pack ({SOURCE}); the safety "
            f"check stopped its line, so Moxie said nothing") in eye.out, eye.out


def test_a_flagged_line_to_the_robots_eye_is_said_and_recorded(tmp_path, capsys):
    """A flag asks the parent to look; it is not a verdict. The line is said with its act,
    its subscription and end_turn, it is in the review queue, and the feed and the log
    name the pack and module."""
    verdict = S.assess(PUNCH_THE_AIR, role=S.MOXIE)
    assert (verdict.action, verdict.categories) == (S.FLAG, ["violence_talk"]), "premise"
    eye = _eye_line(tmp_path, PUNCH_THE_AIR, capsys=capsys)
    resp = eye.resp

    assert resp["result"] == ResultCode.SUCCESS and resp["output"]["text"] == PUNCH_THE_AIR
    assert "<mark" in resp["output"]["markup"], "performed like any other line"
    action = resp["response_actions"][0]
    assert action["function_id"] == "eb_enable_qr", resp
    assert action["event_subscription"]["active"] == [P.QR_EVENT], resp
    assert eye.handed["end_turn"] is True
    assert eye.said == [PUNCH_THE_AIR]

    view = eye.rt.safety_view(eye.dev)
    assert view["unreviewed"] == 1, view
    row = view["events"][0]
    assert (row["side"], row["action"], row["categories"]) == \
        ("moxie", "flag", ["violence_talk"]), row
    assert (f"🧬 a content pack ({SOURCE}) answered {FOUND} with a line the safety check "
            f"flagged: '{PUNCH_THE_AIR[:40]}'") in eye.feed, eye.feed
    assert (f"[runtime] 🧬 {eye.dev}: {FOUND} woke a content pack ({SOURCE}) -> "
            f"'{PUNCH_THE_AIR}' (flagged)") in eye.out, eye.out


def test_a_clean_line_to_the_robots_eye_goes_out_as_it_came(tmp_path, capsys):
    """Nothing matched: the line, the pack's own markup, the act, the subscription and
    end_turn are handed over exactly as before the check existed, nothing is journaled,
    and the feed and the log read as they always did."""
    assert not S.assess(BIG_SMILE, role=S.MOXIE), "premise: nothing in the table matches"
    markup = vocab.mood_mark(2) + BIG_SMILE
    eye = _eye_line(tmp_path, BIG_SMILE, markup=markup, capsys=capsys)
    resp = eye.resp

    assert resp["result"] == ResultCode.SUCCESS and resp["output"]["text"] == BIG_SMILE
    assert resp["output"]["markup"] == markup, "the pack's markup is spoken as written"
    assert resp["response_actions"] == [{
        "output_type": "GLOBAL_RESPONSE", "action": "execute", "function_id": "eb_enable_qr",
        "function_args": ["true"],
        "event_subscription": {"active": [P.QR_EVENT], "clear": False}}], resp
    assert eye.handed["end_turn"] is True
    assert eye.said == [BIG_SMILE]
    assert eye.rt.safety_view(eye.dev)["counts"] == {}
    assert f"🧬 a pack answered {FOUND}: '{BIG_SMILE}'" in eye.feed, eye.feed
    assert (f"[runtime] 🧬 {eye.dev}: {FOUND} woke a content pack -> "
            f"'{BIG_SMILE}'") in eye.out, eye.out


@pytest.mark.parametrize("line, action", [
    ("Found you, Sam! Now tell me your home address.", S.BLOCK),
    ("Found you, Sam! Let's punch the air, hooray!", S.FLAG),
], ids=["blocked", "flagged"])
def test_no_childs_name_in_what_the_eye_check_keeps(tmp_path, capsys, line, action):
    """The child is Sam (`make_runtime`), and a pack's rule may say the name
    (`child.nickname` is a default grant). The review row's excerpt, the stored queue, the
    feed and the log keep no name, as the hello's do. The robot still hears the name in a
    flagged line: only the copies kept for people are masked."""
    assert S.assess(line, role=S.MOXIE).action == action, "premise"
    eye = _eye_line(tmp_path, line, line_only=True, capsys=capsys)
    named = re.compile(r"(?<!\w)sam(?!\w)", re.IGNORECASE)

    rows = eye.rt.safety_view(eye.dev)["events"]
    assert len(rows) == 1 and rows[0]["action"] == action, rows
    assert rows[0]["excerpt"].startswith(f"Found you, {CHILD_MASK}!"), rows[0]
    stored = json.dumps(eye.rt.store.read(eye.dev, S.EVENTS_COLLECTION, []))
    assert not named.search(stored), stored
    assert not any(named.search(text) for text in eye.feed), eye.feed
    assert not named.search(eye.out), eye.out
    assert eye.said == ([] if action == S.BLOCK else [line]), eye.said


def _eye_clock(monkeypatch, at=1_000_000.0):
    """presence.py's clock (`presence.time`: what an event is stamped with on the way in,
    and what the journal's window reads), stopped at `at` and moved by hand, so a blink
    lasts exactly as long as the test says. Only that module's reference moves. The
    hysteresis knobs are pinned to their documented defaults whatever the environment."""
    import moxie_runtime.presence as presence_mod
    clock = types.SimpleNamespace(**{k: getattr(time, k) for k in dir(time)
                                     if not k.startswith("_")})
    clock.now = at
    clock.time = lambda: clock.now
    monkeypatch.setattr(presence_mod, "time", clock)
    monkeypatch.setattr(P, "FLICKER_S", 3.0)
    monkeypatch.setattr(P, "MIN_PRESENT_S", 2.0)
    return clock


def _counts(rt, dev):
    """Each review row's count, newest first (a row with no count stands for one event)."""
    return [row.get("count", 1) for row in rt.safety_view(dev)["events"]]


def test_a_flapping_face_is_one_row_in_the_queue_and_its_count_says_twenty(
        tmp_path, monkeypatch):
    """A face at the frame edge blinks twenty times, half a second in and half out: twenty
    `eb-found-face` with a flicker `eb-lost-target` between each, which the presence model
    reads as one `arrived` and one `left` it came straight back from (vision.md §7.2).
    The pack answers every face with the line the check stops. Each is answered
    NOREPLY_ACK with nothing said; the episode is one row, and its count says 20."""
    clock = _eye_clock(monkeypatch)
    rt, dev, synth = _armed(tmp_path, _EyePack(ASKS_ADDRESS, line_only=True))
    spoke = len(synth.spoken)
    for blink in range(20):
        if blink:
            clock.now += 0.5
            _vision(rt, dev, LOST, event_id=f"lost-{blink}")
            clock.now += 0.5
        resp = _vision(rt, dev, FOUND, event_id=f"found-{blink}")
        assert resp["result"] == ResultCode.NOREPLY_ACK, (blink, resp)
    state = rt.robots[dev].extra["presence"]
    assert (state["faces_seen"], state["announced"]) == (1, "left"), \
        "premise: one arrival, and the one `left` the blinking announces mid-way"
    assert rt.app.perceived == [FOUND] * 20 and synth.spoken[spoke:] == []

    view = rt.safety_view(dev)
    assert len(view["events"]) == 1, view["events"]
    row = view["events"][0]
    assert (row["action"], row["count"]) == ("block", 20), row
    assert view["counts"]["total"] == 1, "the tally counts rows; the row counts the blinks"


def test_a_new_arrival_is_a_new_row(tmp_path, monkeypatch):
    """One row per episode, not one per robot: once the face has been gone FLICKER_S the
    next found is a new arrival, and the same stopped line is the parent's to see again.
    A reconnect starts the presence record over, so its first arrival is new too, though
    it counts as the first face again."""
    clock = _eye_clock(monkeypatch)
    rt, dev, synth = _armed(tmp_path, _EyePack(ASKS_ADDRESS, line_only=True))
    _vision(rt, dev, FOUND, event_id="found-1")
    clock.now += 1.0
    _vision(rt, dev, FOUND, event_id="found-1b")
    assert _counts(rt, dev) == [2]
    rt.robots[dev].extra["presence"] = P.new_state()        # the record a reconnect starts
    clock.now += 1.0
    _vision(rt, dev, FOUND, event_id="found-2")
    assert rt.robots[dev].extra["presence"]["faces_seen"] == 1, "premise: the first face again"
    assert _counts(rt, dev) == [1, 2]
    clock.now += 10.0
    _vision(rt, dev, LOST, event_id="lost-2")                 # a real departure...
    clock.now += P.FLICKER_S
    _vision(rt, dev, FOUND, event_id="found-3")               # ...and a new arrival
    assert rt.robots[dev].extra["presence"]["faces_seen"] == 2, "premise: a second arrival"
    assert _counts(rt, dev) == [1, 1, 2]


def test_outside_an_episode_the_same_line_is_one_row_per_ten_minutes(tmp_path, monkeypatch):
    """No one in view (a card held up and no face found yet, or after they left) is no
    episode: the same stopped line is one row per ten minutes (EYE_REPEAT_WINDOW_S). An
    event less than FLICKER_S after a lost still belongs to the run that lost may yet
    resume."""
    window = 600.0
    clock = _eye_clock(monkeypatch)
    rt, dev, synth = _armed(tmp_path, _EyePack(ASKS_ADDRESS, line_only=True,
                                               events=(FOUND, P.QR_EVENT)))
    start = clock.now

    def card(at):
        clock.now = at
        resp = _vision(rt, dev, P.QR_EVENT, event_id=f"qr-{at}",
                       input_vars={"$eb_qr_value": "hello"})
        assert resp["result"] == ResultCode.NOREPLY_ACK, resp

    card(start)                                  # no one has arrived: a window opens
    card(start + window - 1)                     # inside it: counted on its row
    assert _counts(rt, dev) == [2]
    card(start + window)                         # ten minutes on: a row again
    assert _counts(rt, dev) == [1, 2]

    clock.now = start + 2 * window
    _vision(rt, dev, FOUND, event_id="found")    # an arrival: an episode, its own row
    assert _counts(rt, dev) == [1, 1, 2]
    clock.now += 5.0
    _vision(rt, dev, LOST, event_id="lost")      # gone, and announced as `left`...
    card(clock.now + 1.0)                        # ...but under FLICKER_S: the same run
    assert _counts(rt, dev) == [2, 1, 2]
    card(clock.now + P.FLICKER_S)                # gone FLICKER_S: outside, a new window
    assert _counts(rt, dev) == [1, 2, 1, 2]


def test_a_flapping_face_cannot_push_a_childs_row_out_of_the_queue(tmp_path, monkeypatch):
    """The queue keeps only its newest MAX_EVENTS rows, whatever their escalate or reviewed
    state (narrowed to 5 here, the same rule). The child says something the parent must
    see first, then a pack answers more face events than the queue holds with a line the
    check stops: the child's row is still there."""
    monkeypatch.setattr(S, "MAX_EVENTS", 5)
    rt, dev, synth = _armed(tmp_path, _EyePack(ASKS_ADDRESS, line_only=True))
    drive_turn(rt, dev, "I wish I was dead", event_id="e-child")
    fresh_pool(rt)
    for n in range(S.MAX_EVENTS + 5):
        _vision(rt, dev, FOUND, event_id=f"found-{n}")
    rows = rt.store.read(dev, S.EVENTS_COLLECTION, [])
    child = [r for r in rows if r["side"] == "child"]
    assert len(child) == 1 and child[0]["escalate"] and not child[0]["reviewed"], rows
    assert [r.get("count", 1) for r in rows if r["side"] == "moxie"] == [S.MAX_EVENTS + 5], rows
