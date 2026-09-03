"""
🎭 Telehealth through the REAL runtime — the six verbs, the three gates, the transcript.

A real `MoxieRuntime` with a fake transport (`helpers_runtime.FakeClient`): no broker, no
robot, no gateway, but the actual permit check, the actual mode gate, the actual safety
classifier and the actual markup floor. What each block is here to pin:

  * **the speak round-trip** — exactly one `commands/telehealth` PLAY_OUTPUT and one
    `commands/tts`, the markup valid against the frozen catalog, and the mood the operator
    picked really on the wire;
  * **the mode gate** — speaking at a robot still running its own brain is refused and
    publishes nothing, because two voices in one mouth is the failure a child would see;
  * **the permit gate** — a *pending* robot cannot be puppeted by any verb;
  * **safety** — the operator's line is classified as `MOXIE`; a BLOCK is returned to the
    operator with its reason and nothing is spoken (never silently rewritten, because a
    human is at the keyboard); a FLAG is spoken and journaled;
  * **no brain during a session** (B3) — a `events/remote-chat` that arrives mid-session
    produces no `commands/remote_chat`;
  * **state ingest** — what the robot reported, verbatim, and "never reported" until then;
  * **the status HTTP verbs** the console proxies, driven against a real handler.

Every assumption these exercise is flagged in `mqtt/moxie_sdk/telehealth.py`; nothing here
has run against a physical robot.
"""
import json
import os
import sys
import threading
import urllib.error
import urllib.request

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
sys.path.insert(0, os.path.dirname(__file__))

from helpers_runtime import CountingSynth, FakeClient, make_runtime   # noqa: E402
from moxie_sdk import safety as safety_seam                           # noqa: E402
from moxie_sdk import telehealth as th                                # noqa: E402
from moxie_sdk import vocab                                           # noqa: E402
from moxie_sdk.app import MoxieApp                                    # noqa: E402
from moxie_sdk.store import JsonStore                                 # noqa: E402
from moxie_sdk.stt import VADState                                    # noqa: E402
from moxie_sdk.types import RobotContext                              # noqa: E402

TH_TOPIC = "/devices/{d}/commands/telehealth"
TTS_TOPIC = "/devices/{d}/commands/tts"
CHAT_TOPIC = "/devices/{d}/commands/remote_chat"


class _App(MoxieApp):
    name = "telehealth-test"

    def respond(self, turn):
        from moxie_sdk.types import Reply
        return Reply(text="the brain answered")


@pytest.fixture()
def rt(tmp_path):
    """A permitted, connected robot with puppet mode ON and a session open."""
    runtime, device_id = make_runtime(_App())
    runtime.store = JsonStore(root=str(tmp_path))
    runtime.telehealth_enable(device_id, True)
    runtime.telehealth_session(device_id, "START_SESSION")
    runtime.client = FakeClient()          # forget the enable/start publishes
    return runtime, device_id


def _telehealth_msgs(runtime, device_id):
    return [p["message"] for p in runtime.client.on(TH_TOPIC.format(d=device_id))]


# --------------------------------------------------------------------------- #
# T4 — the speak round-trip
# --------------------------------------------------------------------------- #
def test_speak_publishes_one_play_output_and_one_tts(rt):
    runtime, device_id = rt
    runtime.set_synthesizer(CountingSynth())
    out = runtime.telehealth_speak(device_id, "Hello Sam, I missed you.",
                                   mood="happy", intensity=2)
    assert out["ok"] is True
    msgs = _telehealth_msgs(runtime, device_id)
    assert len(msgs) == 1 and msgs[0]["action"] == "PLAY_OUTPUT"
    assert msgs[0]["output"]["text"] == "Hello Sam, I missed you."
    assert len(runtime.client.on(TTS_TOPIC.format(d=device_id))) == 1


def test_the_markup_validates_against_the_frozen_catalog(rt):
    runtime, device_id = rt
    out = runtime.telehealth_speak(device_id, "Look at that!", mood="surprised")
    assert vocab.validate_markup(out["markup"]) == []


def test_the_mood_the_operator_picked_is_the_mood_on_the_wire(rt):
    """The whole point of the picker: what a human chose reaches the robot's face."""
    runtime, device_id = rt
    for name, value in (("sad", 2), ("curious", 9), ("embarrassed", 10)):
        runtime.client = FakeClient()
        runtime.telehealth_speak(device_id, "Something happened.", mood=name)
        markup = _telehealth_msgs(runtime, device_id)[0]["output"]["markup"]
        assert '+mood+:%d' % value in markup, markup


def test_intensity_reaches_the_wire_as_the_recovered_0_to_2(rt):
    runtime, device_id = rt
    for asked, expected in ((0, 0), (1, 1), (2, 2), (9, vocab.MAX_INTENSITY)):
        runtime.client = FakeClient()
        runtime.telehealth_speak(device_id, "Here we go.", mood="happy",
                                 intensity=asked)
        markup = _telehealth_msgs(runtime, device_id)[0]["output"]["markup"]
        assert '+intensity+:%d' % expected in markup, markup


def test_every_line_is_its_own_utterance_so_each_one_carries_its_mood(rt):
    """Telehealth never streams — one PLAY_OUTPUT per line, chunk 0 of its own utterance.
    If lines were numbered as chunks of one turn, `annotate` would emit the mood on the
    first one only and every later line would arrive faceless."""
    runtime, device_id = rt
    runtime.telehealth_speak(device_id, "First line.", mood="happy")
    runtime.telehealth_speak(device_id, "Second line.", mood="sad")
    first, second = [m["output"]["markup"] for m in _telehealth_msgs(runtime, device_id)]
    assert "+mood+:1" in first and "+mood+:2" in second


def test_an_unknown_mood_is_refused_to_the_operator_and_speaks_nothing(rt):
    runtime, device_id = rt
    out = runtime.telehealth_speak(device_id, "Hello.", mood="sassy")
    assert out["ok"] is False and "sassy" in out["reason"]
    assert runtime.client.published == []


def test_an_empty_line_is_refused(rt):
    runtime, device_id = rt
    assert runtime.telehealth_speak(device_id, "   ")["ok"] is False
    assert runtime.client.published == []


def test_the_operators_line_lands_in_the_transcript(rt):
    runtime, device_id = rt
    runtime.telehealth_speak(device_id, "Good morning.")
    lines = runtime.telehealth_view(device_id)["transcript"]
    assert [(x["who"], x["text"]) for x in lines] == [("operator", "Good morning.")]


# --------------------------------------------------------------------------- #
# T5 — the mode gate, and how the mode is written
# --------------------------------------------------------------------------- #
def test_speaking_with_the_mode_off_refuses_and_publishes_nothing():
    runtime, device_id = make_runtime(_App())
    runtime.client = FakeClient()
    out = runtime.telehealth_speak(device_id, "Hello.")
    assert out["ok"] is False and out["error"] == "not in telehealth mode"
    assert "Be Moxie" in out["reason"]
    assert runtime.client.published == []


def test_enable_flips_moxie_mode_in_the_pushed_config_and_only_in_the_robot_layer():
    runtime, device_id = make_runtime(_App())
    runtime.client = FakeClient()
    runtime.telehealth_enable(device_id, True)
    cfg = runtime.client.on(f"/devices/{device_id}/config")[-1]
    assert cfg["moxie_mode"] == "TELEHEALTH"
    assert runtime._config_overrides[device_id] == {"moxie_mode": 1}
    assert runtime.fleet_config() == {}          # never written to the household layer
    assert runtime.telehealth_enabled(device_id) is True


def test_disable_returns_the_robot_to_its_own_brain():
    runtime, device_id = make_runtime(_App())
    runtime.telehealth_enable(device_id, True)
    runtime.client = FakeClient()
    runtime.telehealth_enable(device_id, False)
    cfg = runtime.client.on(f"/devices/{device_id}/config")[-1]
    assert cfg["moxie_mode"] == "DEFAULT_MODE"
    assert runtime.telehealth_enabled(device_id) is False


def test_disabling_mid_session_ends_the_session_first():
    """A brain-less robot left holding a session nobody will send lines to is the one
    state worse than either end."""
    runtime, device_id = make_runtime(_App())
    runtime.telehealth_enable(device_id, True)
    runtime.telehealth_session(device_id, "START_SESSION")
    runtime.client = FakeClient()
    runtime.telehealth_enable(device_id, False)
    actions = [m["action"] for m in _telehealth_msgs(runtime, device_id)]
    assert actions == ["END_SESSION"]
    assert runtime.telehealth_view(device_id)["in_session"] is False


def test_a_session_cannot_be_started_before_the_mode_is_on():
    runtime, device_id = make_runtime(_App())
    runtime.client = FakeClient()
    out = runtime.telehealth_session(device_id, "START_SESSION")
    assert out["ok"] is False and out["error"] == "not in telehealth mode"
    assert runtime.client.published == []


def test_start_mints_a_session_id_and_end_clears_it(rt):
    runtime, device_id = rt
    view = runtime.telehealth_session(device_id, "END_SESSION")
    assert view["session_id"] == "" and view["in_session"] is False
    started = runtime.telehealth_session(device_id, "START_SESSION")
    assert started["session_id"].startswith("ths-")
    msgs = _telehealth_msgs(runtime, device_id)
    assert [m["action"] for m in msgs] == ["END_SESSION", "START_SESSION"]
    assert msgs[-1]["session_id"] == started["session_id"]


def test_an_unknown_session_verb_is_refused(rt):
    runtime, device_id = rt
    assert runtime.telehealth_session(device_id, "PLAY_OUTPUT")["ok"] is False
    assert runtime.client.published == []


def test_interrupt_publishes_a_message_with_no_output(rt):
    runtime, device_id = rt
    assert runtime.telehealth_interrupt(device_id)["ok"] is True
    msg = _telehealth_msgs(runtime, device_id)[0]
    assert msg["action"] == "INTERRUPT" and "output" not in msg


# --------------------------------------------------------------------------- #
# T6 — the permit gate
# --------------------------------------------------------------------------- #
@pytest.fixture()
def pending(tmp_path):
    """A robot that reached the broker but is NOT on the permit list."""
    runtime = __import__("moxie_runtime").MoxieRuntime(
        app=_App(), allow_unverified_bots=False)
    runtime.store = JsonStore(root=str(tmp_path))
    runtime.client = FakeClient()
    runtime.robots["d_pending"] = RobotContext(device_id="d_pending", child=runtime.child)
    return runtime, "d_pending"


@pytest.mark.parametrize("call", [
    lambda r, d: r.telehealth_enable(d, True),
    lambda r, d: r.telehealth_session(d, "START_SESSION"),
    lambda r, d: r.telehealth_session(d, "END_SESSION"),
    lambda r, d: r.telehealth_speak(d, "hello"),
    lambda r, d: r.telehealth_interrupt(d),
    lambda r, d: r.telehealth_view(d),
])
def test_a_pending_robot_cannot_be_puppeted_by_any_verb(pending, call):
    """A pending robot is by definition a device we have not identified. Puppeting it
    would be the pairing gate's exact failure mode with a microphone attached."""
    runtime, device_id = pending
    out = call(runtime, device_id)
    assert out["ok"] is False and out["error"] == "not permitted"
    assert out["reason"]
    assert runtime.client.published == []


def test_a_robot_that_is_not_connected_is_a_404_shaped_refusal(rt):
    runtime, _ = rt
    out = runtime.telehealth_speak("d_nope", "hello")
    assert out["ok"] is False and "unknown device_id" in out["error"]


# --------------------------------------------------------------------------- #
# T7 — safety: the operator's text is checked, and a block goes BACK to them
# --------------------------------------------------------------------------- #
def _journal(runtime, device_id):
    return runtime.store.read(device_id, safety_seam.EVENTS_COLLECTION, []) or []


def test_a_blocked_operator_line_is_refused_with_its_reason_and_never_spoken(rt):
    runtime, device_id = rt
    out = runtime.telehealth_speak(device_id, "you are a fucking idiot")
    assert out["ok"] is False and out["blocked"] is True
    assert out["categories"] == ["profanity"]
    assert "Profanity" in out["reason"] and "rephrase" in out["reason"]
    assert runtime.client.published == []              # nothing on ANY topic
    rows = _journal(runtime, device_id)
    assert len(rows) == 1 and rows[0]["action"] == "block"
    assert rows[0]["side"] == safety_seam.MOXIE        # judged as words Moxie will say


def test_a_blocked_line_is_not_silently_rewritten(rt):
    """The brain path substitutes a redirect because there is nobody to tell. Here a human
    is at the keyboard: they get the verdict, not a replacement sentence."""
    runtime, device_id = rt
    out = runtime.telehealth_speak(device_id, "you are a fucking idiot")
    assert "spoke" not in out and "markup" not in out
    assert runtime.telehealth_view(device_id)["transcript"] == []


def test_a_flagged_line_is_spoken_and_journaled(rt):
    runtime, device_id = rt
    out = runtime.telehealth_speak(device_id, "The dragon killed the knight.")
    assert out["ok"] is True and out["flagged"] == ["violence_talk"]
    assert len(_telehealth_msgs(runtime, device_id)) == 1
    rows = _journal(runtime, device_id)
    assert len(rows) == 1 and rows[0]["action"] == "flag"


def test_an_ordinary_line_writes_no_journal_row(rt):
    runtime, device_id = rt
    assert runtime.telehealth_speak(device_id, "Shall we read a story?")["ok"] is True
    assert _journal(runtime, device_id) == []


def test_a_safety_stage_that_is_off_does_not_silence_the_operator(tmp_path):
    runtime, device_id = make_runtime(_App())
    runtime.store = JsonStore(root=str(tmp_path))
    runtime.safety = None
    runtime.telehealth_enable(device_id, True)
    runtime.client = FakeClient()
    assert runtime.telehealth_speak(device_id, "Hello there.")["ok"] is True


# --------------------------------------------------------------------------- #
# T8 — no brain during a session (B3)
# --------------------------------------------------------------------------- #
def test_a_remote_chat_during_a_session_gets_no_brain_reply(rt):
    """Whether a brain-less robot still emits `events/remote-chat` is unknown; the design
    has to be correct either way, and a brain reply racing the operator is the one failure
    a child would see as broken."""
    runtime, device_id = rt
    runtime._on_remote_chat(device_id, runtime.robots[device_id], json.dumps(
        {"command": "prompt", "backend": "router", "event_id": "e1",
         "speech": "hello Moxie"}))
    runtime._pool.shutdown(wait=True)
    assert runtime.client.on(CHAT_TOPIC.format(d=device_id)) == []
    assert any("ignored a remote-chat" in n["text"] for n in runtime.recent)


def test_the_brain_answers_again_once_the_session_ends(rt):
    runtime, device_id = rt
    runtime.telehealth_session(device_id, "END_SESSION")
    runtime._on_remote_chat(device_id, runtime.robots[device_id], json.dumps(
        {"command": "prompt", "backend": "router", "event_id": "e2",
         "speech": "hello Moxie"}))
    runtime._pool.shutdown(wait=True)
    replies = runtime.client.on(CHAT_TOPIC.format(d=device_id))
    assert replies and replies[-1]["output"]["text"] == "the brain answered"


# --------------------------------------------------------------------------- #
# T9 — state ingest, and the honest "never reported"
# --------------------------------------------------------------------------- #
def test_the_state_starts_empty_rather_than_assumed():
    runtime, device_id = make_runtime(_App())
    view = runtime.telehealth_view(device_id)
    assert view["state"] == "" and view["state_at"] is None


def test_an_activity_log_event_updates_the_reported_state(rt):
    runtime, device_id = rt
    runtime._on_activity(device_id, json.dumps(
        {"subtopic": "telehealth",
         "message": {"state": "IN_SESSION", "timestamp": 1700000000000}}))
    view = runtime.telehealth_view(device_id)
    assert view["state"] == "IN_SESSION" and view["state_at"] == 1700000000.0


def test_an_unknown_reported_state_is_kept_and_called_out(rt):
    runtime, device_id = rt
    runtime._on_activity(device_id, json.dumps(
        {"subtopic": "telehealth", "message": {"state": "CALIBRATING"}}))
    assert runtime.telehealth_view(device_id)["state"] == "CALIBRATING"
    assert any("not a state we know" in n["text"] for n in runtime.recent)


def test_an_exiting_report_cannot_resurrect_a_session_we_closed(rt):
    """Found by the SIL run: the robot reports EXITING (carrying the session id) *after*
    END_SESSION, and adopting that id put the runtime back "in session" — so the next
    disable published a second END_SESSION at a robot that had already torn down."""
    runtime, device_id = rt
    session_id = runtime.telehealth_view(device_id)["session_id"]
    runtime.telehealth_session(device_id, "END_SESSION")
    runtime.client = FakeClient()
    runtime._on_activity(device_id, json.dumps(
        {"subtopic": "telehealth",
         "message": {"state": "EXITING", "session_id": session_id}}))
    assert runtime.telehealth_view(device_id)["in_session"] is False
    runtime.telehealth_enable(device_id, False)
    assert _telehealth_msgs(runtime, device_id) == []


def test_a_supervisor_that_restarted_mid_session_picks_the_session_back_up(rt):
    """The other half of the same rule: a robot that says it IS in a session tells a
    runtime with no record of one what that session is."""
    runtime, device_id = rt
    runtime.telehealth_session(device_id, "END_SESSION")
    runtime._on_activity(device_id, json.dumps(
        {"subtopic": "telehealth",
         "message": {"state": "IN_SESSION", "session_id": "ths-fromrobot"}}))
    view = runtime.telehealth_view(device_id)
    assert view["in_session"] is True and view["session_id"] == "ths-fromrobot"


def test_a_telehealth_event_is_not_mistaken_for_a_query(rt):
    """The activity log is multiplexed; the telehealth subtopic must not fall into the
    CloudQuery branch and get answered with a `query_result`."""
    runtime, device_id = rt
    runtime._on_activity(device_id, json.dumps(
        {"subtopic": "telehealth", "query": "schedule",
         "message": {"state": "READY"}}))
    assert runtime.client.on(f"/devices/{device_id}/commands/query_result") == []


# --------------------------------------------------------------------------- #
# The transcript ring: the child's side, and the privacy gate on it
# --------------------------------------------------------------------------- #
class _Fixed:
    """A `moxie_sdk.stt.Transcriber` that always hears the same thing (rule 9: a `client=`
    style seam, so no optional dependency is needed to exercise the path)."""
    name = "fixed"

    def transcribe(self, audio, sample_rate=16000):
        return "I built a rocket"


def test_the_childs_words_reach_the_transcript_during_a_session(rt):
    runtime, device_id = rt
    runtime.set_transcriber(_Fixed())
    runtime.feed_stt(device_id, VADState.START_OF_SPEECH, b"\x00\x01")
    runtime.feed_stt(device_id, VADState.END_OF_SPEECH, b"\x00\x01")
    lines = runtime.telehealth_view(device_id)["transcript"]
    assert [(x["who"], x["text"]) for x in lines] == [("child", "I built a rocket")]


def test_nothing_of_the_child_is_kept_outside_a_session(rt):
    runtime, device_id = rt
    runtime.telehealth_session(device_id, "END_SESSION")
    runtime.set_transcriber(_Fixed())
    runtime.feed_stt(device_id, VADState.END_OF_SPEECH, b"\x00\x01")
    assert runtime.telehealth_view(device_id)["transcript"] == []


def test_under_no_data_the_ring_keeps_operator_lines_only(rt):
    """The same `LoggingPolicy` check the safety journal uses. A parent who set NO_DATA
    said "keep none of my child's words"; the operator's own lines are still the record of
    what a third party said to their child."""
    from moxie_sdk.cloud_config import LoggingPolicy
    runtime, device_id = rt
    runtime.update_config(device_id, logging_policy=int(LoggingPolicy.NO_DATA))
    runtime.set_transcriber(_Fixed())
    runtime.feed_stt(device_id, VADState.END_OF_SPEECH, b"\x00\x01")
    runtime.telehealth_speak(device_id, "That sounds fun.")
    lines = runtime.telehealth_view(device_id)["transcript"]
    assert [x["who"] for x in lines] == ["operator"]


def test_the_transcript_is_bounded(rt):
    runtime, device_id = rt
    for i in range(th.TRANSCRIPT_MAX + 20):
        runtime._telehealth_note(device_id, th.OPERATOR, f"line {i}")
    lines = runtime.telehealth_view(device_id)["transcript"]
    assert len(lines) == th.TRANSCRIPT_MAX
    assert lines[-1]["text"] == f"line {th.TRANSCRIPT_MAX + 19}"


def test_the_view_survives_the_robot_dropping_off_wifi(rt):
    """An operator whose robot just lost Wi-Fi should still see what was said, and be told
    it is offline — not handed an empty card."""
    runtime, device_id = rt
    runtime.telehealth_speak(device_id, "Are you still there?")
    runtime._device_disconnect(device_id)
    view = runtime.telehealth_view(device_id)
    assert view["ok"] is True and view["online"] is False
    assert view["transcript"][-1]["text"] == "Are you still there?"


def test_the_view_carries_the_vocabulary_the_card_renders(rt):
    runtime, device_id = rt
    view = runtime.telehealth_view(device_id)
    assert len(view["moods"]) == 11 and view["max_intensity"] == 2


# --------------------------------------------------------------------------- #
# Bedtime (B4): a warning, never a gate
# --------------------------------------------------------------------------- #
def test_the_bedtime_warning_is_reported_and_the_line_is_still_sent(rt):
    """We do not know whether a robot suppresses a puppet line inside its bedtime window,
    so the operator is told the truth and the line goes anyway. Guessing either way would
    be worse than saying so."""
    import datetime
    runtime, device_id = rt

    # A window centred on *now*, so this test cannot depend on the hour it runs at.
    # It used to say ["00:00", "23:59"], which reads as "all day" but is not: the helper
    # compares `start <= cur < end`, so that window is false for exactly the minute
    # 23:59, and the test failed there once a day. A now±1h window always contains now,
    # including when it wraps midnight — `in_bedtime` handles `start > end` explicitly,
    # and the wrap is the normal case for a real bedtime (20:30-07:00).
    now = datetime.datetime.now()
    start = (now - datetime.timedelta(hours=1)).strftime("%H:%M")
    end = (now + datetime.timedelta(hours=1)).strftime("%H:%M")
    runtime.update_config(device_id, weekday_bedtime=[start, end],
                          weekend_bedtime=[start, end])   # both, so the weekday never matters
    runtime.client = FakeClient()
    view = runtime.telehealth_view(device_id)
    assert view["in_bedtime"] is True, f"window {start}-{end} must contain {now:%H:%M}"
    assert runtime.telehealth_speak(device_id, "Sleep well.")["ok"] is True
    assert len(_telehealth_msgs(runtime, device_id)) == 1

    # …and the pure helper the view reads is exactly the runtime's own answer.
    from moxie_sdk.cloud_config import in_bedtime
    assert in_bedtime(runtime.effective_config(device_id), now) is True

    # Plus a fully deterministic pair — no wall clock anywhere — so the helper's real
    # semantics stay pinned even if the block above were ever loosened: a normal wrapping
    # night contains 23:00 and excludes noon.
    night = {"weekday_bedtime": ["20:30", "07:00"], "weekend_bedtime": ["20:30", "07:00"]}
    assert in_bedtime(night, datetime.datetime(2026, 9, 2, 23, 0)) is True
    assert in_bedtime(night, datetime.datetime(2026, 9, 2, 12, 0)) is False


# --------------------------------------------------------------------------- #
# The status HTTP verbs the console proxies — driven against the real handler
# --------------------------------------------------------------------------- #
@pytest.fixture()
def served(rt):
    """The runtime's real `_start_status_server` on a free port."""
    import socket
    runtime, device_id = rt
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    runtime._start_status_server(port)
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):                       # the server starts on a thread
        try:
            urllib.request.urlopen(f"{base}/status", timeout=1).read()
            break
        except Exception:
            threading.Event().wait(0.05)
    return runtime, device_id, base


def _call(base, device_id, payload=None):
    url = f"{base}/telehealth?device_id={device_id}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode() or "{}"), e.code


def test_get_telehealth_serves_the_view(served):
    runtime, device_id, base = served
    out, code = _call(base, device_id)
    assert code == 200 and out["ok"] is True and out["enabled"] is True


def test_get_telehealth_for_an_unknown_device_is_a_404(served):
    _, _, base = served
    out, code = _call(base, "d_nope")
    assert code == 404 and out["ok"] is False


def test_post_telehealth_speaks(served):
    runtime, device_id, base = served
    out, code = _call(base, device_id, {"action": "speak", "text": "Hello there.",
                                        "mood": "happy", "intensity": 1})
    assert code == 200 and out["ok"] is True and out["spoke"] == "Hello there."
    assert len(_telehealth_msgs(runtime, device_id)) == 1


def test_a_safety_block_over_http_is_a_400_carrying_the_reason(served):
    """The acceptance criterion in one call: refused to the operator, with the reason,
    and nothing spoken."""
    runtime, device_id, base = served
    out, code = _call(base, device_id, {"action": "speak",
                                        "text": "you are a fucking idiot"})
    assert code == 400
    assert out["ok"] is False and out["blocked"] is True
    assert "Profanity" in out["reason"]
    assert runtime.client.published == []


def test_the_mode_gate_over_http_is_a_400_the_console_can_act_on(served):
    runtime, device_id, base = served
    _call(base, device_id, {"action": "disable"})
    runtime.client = FakeClient()
    out, code = _call(base, device_id, {"action": "speak", "text": "Hello."})
    assert code == 400 and "Be Moxie" in out["reason"]
    assert runtime.client.published == []


def test_every_verb_round_trips_over_http(served):
    runtime, device_id, base = served
    for payload in ({"action": "end"}, {"action": "disable"}, {"action": "enable"},
                    {"action": "start"}, {"action": "speak", "text": "Hi."},
                    {"action": "interrupt"}, {"action": "state"}, {"action": "end"}):
        out, code = _call(base, device_id, payload)
        assert code == 200 and out["ok"] is True, (payload, code, out)
    actions = [m["action"] for m in _telehealth_msgs(runtime, device_id)]
    # `disable` publishes no second END_SESSION: the first `end` already closed the
    # session, and a verb that has nothing to end must not invent traffic.
    assert actions == ["END_SESSION", "START_SESSION", "PLAY_OUTPUT",
                       "INTERRUPT", "UPDATE_STATE", "END_SESSION"]


def test_an_unknown_verb_is_a_400(served):
    _, device_id, base = served
    out, code = _call(base, device_id, {"action": "dance"})
    assert code == 400 and out["ok"] is False
