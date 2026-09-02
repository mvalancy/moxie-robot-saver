"""
Streamed replies — the child hears the FIRST sentence, not the whole answer.

PR #14 stopped the silence of a slow brain with one filler line; it did not shorten the
wait for real words, so a 45 s turn still went quiet at ~26 s
(docs/architecture/implementation-plan.md:138). This slice streams the answer: every
finished sentence goes out as its own `RemoteChatResponse` — `result=REPLY_PENDING` with
a `chunk_num` (RemoteChat.proto field 22), closed by a `SUCCESS` carrying
`consistency_control.is_completed` (field 18) — which is the contract's own "one
event_id, several responses" shape (docs/reverse-engineering/protocol/
remote-chat-protocol.md:26,:63; docs/architecture/mqtt-and-conversation.md §4.5).

**No sleeps.** The fake brain yields each chunk only when the test opens that chunk's
`Event`, and the fake transport is a `Condition` a test waits on, so every ordering here
is causal rather than wall-clock. The two bounded waits that remain are the honest ones:
"a filler appeared inside the budget" and "a THIRD filler never appeared".

Covered: a fast stream (chunk numbering, one constant event_id, every chunk synthesized,
the final one completed); a late first token (filler, then the stream); a mid-answer
stall (a second filler, and never a third); the stale guard cancelling a stream; a
streaming failure falling back to the ordinary reply; `MOXIE_STREAMING=0` reproducing
today's single-reply wire byte for byte; LLMApp's own streaming path (the JSON envelope
decoded incrementally, a leading action tag lifted onto chunk 0, per-chunk markup); and
the SIL client joining the chunks of one turn.
"""
import json
import os
import sys
import threading

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
sys.path.insert(0, os.path.join(REPO, "sim"))
sys.path.insert(0, os.path.dirname(__file__))

from helpers_runtime import (CHAT_TOPIC, CountingSynth, LatchClient,  # noqa: E402
                             make_runtime)
from moxie_sdk.app import MoxieApp                                   # noqa: E402
from moxie_sdk.filler import FILLERS                                 # noqa: E402
from moxie_sdk.types import Reply, ReplyChunk, ResultCode            # noqa: E402

TTS_TOPIC = "/devices/{device_id}/commands/tts"
FILLER_TEXTS = [text for (text, _markup) in FILLERS]

# Long enough that a loaded CI box never trips it, short enough that a real hang fails
# the test instead of hanging the suite.
PATIENCE = 10.0
BUDGET = 0.2


# --------------------------------------------------------------------------- fakes
class Plan:
    """One scripted turn's stream. The test opens `open[k]` to let chunk k out."""

    def __init__(self, chunks, ready=False):
        self.chunks = list(chunks)
        self.open = [threading.Event() for _ in self.chunks]
        self.at = [threading.Event() for _ in self.chunks]
        self.closed = threading.Event()          # set if the runtime cancelled us
        if ready:
            for ev in self.open:
                ev.set()

    def release_all(self):
        for ev in self.open:
            ev.set()


def plan(*chunks, ready=True):
    return Plan(chunks, ready=ready)


def chunk(text, final=False, **kw):
    return ReplyChunk(text=text, final=final, **kw)


class ScriptedStreamApp(MoxieApp):
    """A brain that streams exactly what the test scripted, one gated chunk at a time."""
    name = "scripted-stream"

    def __init__(self, *plans, fallback="I thought about it all at once."):
        self.plans = list(plans)
        self.fallback = fallback
        self.stream_calls = 0
        self.respond_calls = 0
        self._lock = threading.Lock()

    def respond(self, turn):
        with self._lock:
            self.respond_calls += 1
        return Reply(text=self.fallback)

    def respond_stream(self, turn):
        with self._lock:
            i = self.stream_calls
            self.stream_calls += 1
        if i >= len(self.plans):
            return None                          # later turns are not scripted
        return self._gen(self.plans[i])

    def _gen(self, p):
        try:
            for k, item in enumerate(p.chunks):
                p.at[k].set()
                assert p.open[k].wait(PATIENCE), f"the test never released chunk {k}"
                if isinstance(item, BaseException):
                    raise item
                yield item
        except GeneratorExit:
            p.closed.set()
            raise


class NonStreamingApp(MoxieApp):
    """Today's brain: `respond` only, `respond_stream` inherited (returns None)."""
    name = "plain"

    def respond(self, turn):
        return Reply(text=f"You said: {turn.speech}")


# --------------------------------------------------------------------------- harness
def stream_runtime(app, *, budget=5.0, device_id="d_stream", synth=None, streaming=None):
    rt, dev = make_runtime(app, device_id=device_id)
    rt.client = LatchClient()
    rt.brain_budget_s = budget
    if streaming is not None:
        rt.streaming = streaming
    if synth is not None:
        rt.set_synthesizer(synth)
    return rt, dev


def push(rt, device_id, speech, event_id):
    rt._on_remote_chat(device_id, rt.robots[device_id],
                       json.dumps({"command": "prompt", "backend": "router",
                                   "event_id": event_id, "speech": speech}))


def chats(rt, device_id):
    return rt.client.on(CHAT_TOPIC.format(device_id=device_id))


def fillers(payloads):
    return [p for (_t, p) in payloads
            if (p.get("output") or {}).get("text") in FILLER_TEXTS]


# ------------------------------------------------------------------ (i) fast stream
def test_a_streamed_answer_is_chunks_then_one_completed_success():
    """Three sentences → REPLY_PENDING 0, REPLY_PENDING 1, SUCCESS 2 + is_completed."""
    lines = ["The moon looks different every night.",
             "That is because sunlight hits it from a new angle.",
             "Isn't that neat?"]
    app = ScriptedStreamApp(plan(chunk(lines[0]), chunk(lines[1]),
                                 chunk(lines[2], final=True)))
    synth = CountingSynth()
    rt, dev = stream_runtime(app, synth=synth)
    push(rt, dev, "why does the moon change shape?", "evt-stream")
    rt._pool.shutdown(wait=True)

    replies = chats(rt, dev)
    assert [r["output"]["text"] for r in replies] == lines
    assert [r["result"] for r in replies] == ["REPLY_PENDING", "REPLY_PENDING", "SUCCESS"]
    assert [r["chunk_num"] for r in replies] == [0, 1, 2], "chunk_num must be monotonic"
    assert {r["event_id"] for r in replies} == {"evt-stream"}, "one turn, one event_id"
    assert [r["consistency_control"] for r in replies] == [
        {"is_completed": False}, {"is_completed": False}, {"is_completed": True}]
    # Every chunk is spoken, in order, and the SIM can order them by chunk_num.
    assert synth.spoken == lines
    tts = rt.client.on(TTS_TOPIC.format(device_id=dev))
    assert [t["chunk_num"] for t in tts] == [0, 1, 2]
    assert {t["event_id"] for t in tts} == {"evt-stream"}
    # The whole answer — not just the last sentence — is what Moxie remembers saying.
    assert rt.history[dev][-1] == {"role": "assistant", "content": " ".join(lines)}
    assert app.respond_calls == 0, "streaming must not also call the blocking path"


def test_a_one_sentence_stream_is_wire_identical_to_a_plain_reply():
    """The single-chunk case must stay byte-identical: chunk 0 / not-streaming is the
    proto default, so nothing downstream has to learn about chunking to keep working."""
    app = ScriptedStreamApp(plan(chunk("Hi Sam!", final=True)))
    rt, dev = stream_runtime(app)
    push(rt, dev, "hello", "evt-solo")
    rt._pool.shutdown(wait=True)
    (reply,) = chats(rt, dev)
    assert reply["result"] == "SUCCESS" and reply["output"]["text"] == "Hi Sam!"
    assert "chunk_num" not in reply and "consistency_control" not in reply


def test_a_streamed_action_rides_on_its_own_chunk():
    """A `<launch:DRAW>` the model wrote in sentence 1 must reach the wire on chunk 0,
    not be held until the answer ends."""
    from moxie_sdk.types import Action, ActionType
    app = ScriptedStreamApp(plan(
        chunk("Yes, let's go make a picture together.",
              actions=[Action(type=ActionType.LAUNCH, module_id="DRAW")]),
        chunk("I will grab my crayons!", final=True)))
    rt, dev = stream_runtime(app)
    push(rt, dev, "can we draw?", "evt-act")
    rt._pool.shutdown(wait=True)
    first, last = chats(rt, dev)
    assert first["response_actions"] == [
        {"output_type": "GLOBAL", "action": "launch", "module_id": "DRAW",
         "content_id": None}]
    assert "response_actions" not in last


# ------------------------------------------------------- (ii) a late first token
def test_a_late_first_token_gets_a_filler_and_then_the_stream():
    app = ScriptedStreamApp(plan(chunk("Here comes the answer at last."),
                                 chunk("All done!", final=True), ready=False))
    rt, dev = stream_runtime(app, budget=BUDGET)
    push(rt, dev, "why is the sky blue?", "evt-late")

    assert rt.client.wait_for(lambda pub: fillers(pub), PATIENCE), "no filler published"
    first = chats(rt, dev)[0]
    assert first["result"] == "REPLY_PENDING" and first["chunk_num"] == 0
    assert first["consistency_control"] == {"is_completed": False}
    assert first["output"]["text"] in FILLER_TEXTS
    assert first["output"]["markup"] != first["output"]["text"], "filler carries markup"
    assert first["end_turn"] is False, "the turn is not over — the answer is coming"

    rt.brain_budget_s = 0                        # no more fillers; just the answer
    app.plans[0].release_all()
    rt._pool.shutdown(wait=True)

    replies = chats(rt, dev)
    assert [r["output"]["text"] for r in replies[-2:]] == [
        "Here comes the answer at last.", "All done!"]
    assert [r["chunk_num"] for r in replies] == list(range(len(replies)))
    assert replies[-1]["result"] == "SUCCESS"
    assert replies[-1]["consistency_control"] == {"is_completed": True}
    assert len(fillers(rt.client.published)) == 1


# ------------------------------------------------------- (iii) a mid-answer stall
def test_a_stalled_stream_re_arms_the_filler_once_and_never_a_third_time():
    """One filler buys ~20 s; a 45 s brain outlives it. So a stall mid-answer earns one
    more line — and then the runtime stops, because a robot that only ever says it is
    thinking is worse than a quiet one (`MAX_FILLERS_PER_TURN`)."""
    app = ScriptedStreamApp(plan(chunk("First, the moon has no light of its own."),
                                 chunk("It borrows sunlight!", final=True), ready=False))
    rt, dev = stream_runtime(app, budget=BUDGET)
    push(rt, dev, "why does the moon glow?", "evt-stall")

    # 1) the first token is late → filler #1
    assert rt.client.wait_for(lambda pub: len(fillers(pub)) >= 1, PATIENCE)
    app.plans[0].open[0].set()
    assert rt.client.wait_for(
        lambda pub: any((p.get("output") or {}).get("text", "").startswith("First,")
                        for (_t, p) in pub), PATIENCE)

    # 2) the stream then stalls mid-answer → filler #2
    assert rt.client.wait_for(lambda pub: len(fillers(pub)) >= 2, PATIENCE)
    # 3) ...and never a third, however long it stays stalled.
    assert not rt.client.wait_for(lambda pub: len(fillers(pub)) >= 3, 1.0), \
        "the filler budget of 2 per turn was not honored"

    app.plans[0].release_all()
    rt._pool.shutdown(wait=True)
    replies = chats(rt, dev)
    heard = [r["output"]["text"] for r in replies]
    assert len(fillers(rt.client.published)) == 2
    assert heard[0] in FILLER_TEXTS and heard[1] == "First, the moon has no light of its own."
    assert heard[-1] == "It borrows sunlight!"
    assert [r["chunk_num"] for r in replies] == list(range(len(replies)))
    assert replies[-1]["result"] == "SUCCESS"
    assert replies[-1]["consistency_control"] == {"is_completed": True}


def test_two_fillers_in_one_turn_are_never_the_same_line():
    app = ScriptedStreamApp(plan(chunk("The answer, finally.", final=True), ready=False))
    rt, dev = stream_runtime(app, budget=BUDGET)
    push(rt, dev, "tell me something", "evt-rotate")
    assert rt.client.wait_for(lambda pub: len(fillers(pub)) >= 2, PATIENCE)
    said = [(p.get("output") or {}).get("text") for p in fillers(rt.client.published)]
    assert said[0] != said[1], said
    app.plans[0].release_all()
    rt._pool.shutdown(wait=True)


# ------------------------------------------------------------ (iv) the stale guard
def test_a_newer_turn_cancels_the_stream_of_the_old_one():
    """A child who gives up and asks something else must never be answered about the
    abandoned question — not even by a chunk already in flight."""
    old = plan(chunk("The OLD answer starts here."),
               chunk("...and the OLD answer ends here.", final=True), ready=False)
    app = ScriptedStreamApp(old)                 # turn 2 is unscripted → plain respond
    rt, dev = stream_runtime(app, budget=0)      # no fillers: this is about the guard
    push(rt, dev, "why is the sky blue?", "evt-old")
    assert old.at[0].wait(PATIENCE), "the stream never started"
    old.open[0].set()
    assert rt.client.wait_for(lambda pub: pub, PATIENCE)

    push(rt, dev, "what is a whale?", "evt-new")   # the child moves on
    old.open[1].set()                              # the abandoned stream produces more
    rt._pool.shutdown(wait=True)

    texts = [r["output"]["text"] for r in chats(rt, dev)]
    assert "...and the OLD answer ends here." not in texts
    assert app.fallback in texts, "the NEW question was answered"
    assert old.closed.is_set(), "a cancelled stream must be closed, not left running"
    # The abandoned turn's chunk 0 was published before the child moved on (it was
    # already true then); nothing after it carries the old event_id.
    old_ids = [r for r in chats(rt, dev) if r["event_id"] == "evt-old"]
    assert [r["output"]["text"] for r in old_ids] == ["The OLD answer starts here."]


# --------------------------------------------------- (v) streaming error → fallback
def test_a_stream_that_dies_before_a_word_falls_back_to_one_success():
    app = ScriptedStreamApp(plan(RuntimeError("gateway does not stream")))
    rt, dev = stream_runtime(app)
    push(rt, dev, "hello", "evt-boom")
    rt._pool.shutdown(wait=True)
    (reply,) = chats(rt, dev)
    assert reply["result"] == "SUCCESS"
    assert reply["output"]["text"] == app.fallback
    assert "chunk_num" not in reply and "consistency_control" not in reply
    assert app.respond_calls == 1


def test_a_stream_that_dies_mid_answer_still_closes_the_sequence():
    """Words already spoken cannot be unsaid, so the turn is closed rather than
    restarted — the robot must never be left waiting for a chunk that will not come."""
    app = ScriptedStreamApp(plan(chunk("The first half arrived fine."),
                                 RuntimeError("connection reset")))
    rt, dev = stream_runtime(app)
    push(rt, dev, "hello", "evt-halfboom")
    rt._pool.shutdown(wait=True)
    replies = chats(rt, dev)
    assert [r["result"] for r in replies] == ["REPLY_PENDING", "SUCCESS"]
    assert replies[-1]["consistency_control"] == {"is_completed": True}
    assert replies[0]["output"]["text"] == "The first half arrived fine."
    assert app.respond_calls == 0, "half an answer must not be re-asked"


def test_an_app_that_streams_nothing_still_ends_the_turn():
    app = ScriptedStreamApp(plan())
    rt, dev = stream_runtime(app)
    push(rt, dev, "hello", "evt-empty")
    rt._pool.shutdown(wait=True)
    (reply,) = chats(rt, dev)
    assert reply["result"] == "SUCCESS" and "chunk_num" not in reply


# ------------------------------------------------------ (vi) the MOXIE_STREAMING knob
def test_streaming_off_reproduces_todays_single_reply():
    app = ScriptedStreamApp(plan(chunk("one."), chunk("two.", final=True)))
    rt, dev = stream_runtime(app, streaming=False)
    push(rt, dev, "hello", "evt-off")
    rt._pool.shutdown(wait=True)
    (reply,) = chats(rt, dev)
    assert reply["output"]["text"] == app.fallback
    assert "chunk_num" not in reply and "consistency_control" not in reply
    assert app.stream_calls == 0, "MOXIE_STREAMING=0 must not even ask for a stream"


def test_a_non_streaming_app_is_untouched():
    """echo / content / webhook inherit `respond_stream` → None → today's path exactly."""
    rt, dev = stream_runtime(NonStreamingApp())
    assert rt.streaming is True
    push(rt, dev, "hello", "evt-plain")
    rt._pool.shutdown(wait=True)
    (reply,) = chats(rt, dev)
    assert reply["output"]["text"] == "You said: hello"
    assert "chunk_num" not in reply and "consistency_control" not in reply


@pytest.mark.parametrize("value,expected", [
    ("0", False), ("off", False), ("false", False), ("no", False), ("", True),
    ("1", True), ("yes", True), (None, True)])
def test_the_streaming_knob_reads_the_environment(value, expected, monkeypatch):
    import moxie_runtime
    if value is None:
        monkeypatch.delenv("MOXIE_STREAMING", raising=False)
    else:
        monkeypatch.setenv("MOXIE_STREAMING", value)
    rt = moxie_runtime.MoxieRuntime(app=NonStreamingApp())
    assert rt.streaming is expected
    assert moxie_runtime.MoxieRuntime(app=NonStreamingApp(), streaming=False).streaming is False
    assert moxie_runtime.MoxieRuntime(app=NonStreamingApp(), streaming=True).streaming is True


def test_config_exposes_the_knob(monkeypatch):
    monkeypatch.setenv("MOXIE_STREAMING", "0")
    sys.modules.pop("config", None)
    import config                                       # noqa: E402
    assert config.STREAMING is False
    sys.modules.pop("config", None)


# ------------------------------------------------------------- (vii) the SIL client
def _rcr(event_id, text, chunk_num=None, result="SUCCESS", completed=None):
    from moxie_sdk.wire import build_chat_response
    return build_chat_response(event_id, text, backend="router",
                               result=getattr(ResultCode, result),
                               chunk_num=chunk_num, is_completed=completed)


def _virtual_moxie():
    pytest.importorskip("paho.mqtt.client")
    from virtual_moxie import VirtualMoxie
    return VirtualMoxie("127.0.0.1", 1, verbose=False)


def test_the_sil_client_joins_the_chunks_of_one_turn():
    vm = _virtual_moxie()
    vm._on_chat_reply(_rcr("E", "Hmm, let me think.", 0, "REPLY_PENDING", False))
    assert not vm.got_reply.is_set(), "a filler is not the answer"
    vm._on_chat_reply(_rcr("E", "The moon has no light of its own.", 1,
                           "REPLY_PENDING", False))
    assert not vm.got_reply.is_set(), "a pending chunk is not the answer either"
    vm._on_chat_reply(_rcr("E", "It borrows sunlight!", 2, "SUCCESS", True))
    assert vm.got_reply.is_set()
    assert vm.reply_text == ("Hmm, let me think. The moon has no light of its own. "
                            "It borrows sunlight!")
    assert vm.reply_payload["output"]["text"] == "It borrows sunlight!"


def test_the_sil_client_orders_chunks_by_chunk_num_not_arrival():
    vm = _virtual_moxie()
    vm._on_chat_reply(_rcr("E", "second.", 1, "REPLY_PENDING", False))
    vm._on_chat_reply(_rcr("E", "first.", 0, "REPLY_PENDING", False))
    vm._on_chat_reply(_rcr("E", "third.", 2, "SUCCESS", True))
    assert vm.reply_text == "first. second. third."


def test_the_sil_client_still_handles_a_plain_single_reply():
    vm = _virtual_moxie()
    vm._on_chat_reply(_rcr("E", "You said: hello"))
    assert vm.got_reply.is_set() and vm.reply_text == "You said: hello"


def test_the_sil_client_forgets_the_previous_turn():
    vm = _virtual_moxie()
    vm._on_chat_reply(_rcr("A", "first turn"))
    vm._reset_turn()
    vm._on_chat_reply(_rcr("B", "second turn"))
    assert vm.reply_text == "second turn"


# ------------------------------------------------------------- LLMApp's own stream
class _FakeStream:
    """The openai client's `chat.completions` seam: scripted tokens, or a failure."""

    def __init__(self, tokens=(), whole="", fail=None):
        self.tokens, self.whole, self.fail = list(tokens), whole, fail
        self.stream_calls = self.whole_calls = 0

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kw):
        if kw.get("stream"):
            self.stream_calls += 1
            if self.fail is not None:
                raise self.fail
            return iter([{"choices": [{"delta": {"content": t}}]} for t in self.tokens])
        self.whole_calls += 1
        return type("R", (), {"choices": [type("C", (), {
            "message": type("M", (), {"content": self.whole})()})()]})()


def _llm_app(fake, **kw):
    from moxie_sdk.apps.llm_app import LLMApp
    app = LLMApp(base_url="http://127.0.0.1:1/v1", api_key="k", model="m", **kw)
    app._client = fake
    return app


def _turn(speech="why does the moon change shape?"):
    from moxie_sdk.types import ChildProfile, RobotContext, Turn
    return Turn(robot=RobotContext(device_id="d_x", child=ChildProfile(nickname="Sam")),
                speech=speech)


def _tokens(text, size=7):
    return [text[i:i + size] for i in range(0, len(text), size)]


def test_llm_app_streams_the_say_field_sentence_by_sentence():
    """The expressive prompt streams a JSON object; only the value of "say" is spoken,
    and mood/gesture (which arrive last) style the closing chunk."""
    raw = ('{"say": "The moon has no light of its own. It borrows sunlight from the sun. '
           'Isn\'t that neat?", "mood": "surprised", "gesture": "big"}')
    fake = _FakeStream(tokens=_tokens(raw))
    chunks = list(_llm_app(fake).respond_stream(_turn()))
    assert [c.text for c in chunks] == [
        "The moon has no light of its own.",
        "It borrows sunlight from the sun.",
        "Isn't that neat?"]
    assert [c.final for c in chunks] == [False, False, True]
    assert fake.stream_calls == 1 and fake.whole_calls == 0
    # markup is local string work, never a second model call: rules mid-stream, the
    # model's own mood/gesture on the closing chunk.
    assert '+mood+:0' in chunks[0].markup and "Gesture_Talk" in chunks[0].markup
    assert "Gesture_Question" in chunks[1].markup or chunks[1].markup   # rule-based
    assert '+mood+:5' in chunks[-1].markup, "the model's mood reaches the last chunk"
    assert "Gesture_Large" in chunks[-1].markup


def test_llm_app_lifts_a_leading_action_tag_onto_the_first_chunk():
    """Our prompt convention puts the tag at the FRONT, so it lands on chunk 0 — and is
    never spoken."""
    raw = ('{"say": "<exit>Bye Sam, I loved hearing about your day. '
           'See you tomorrow!", "mood": "positive", "gesture": "talk"}')
    chunks = list(_llm_app(_FakeStream(tokens=_tokens(raw))).respond_stream(_turn("bye")))
    assert chunks[0].text == "Bye Sam, I loved hearing about your day."
    assert "<exit>" not in "".join(c.text for c in chunks)
    assert [a.type.value for a in chunks[0].actions] == ["exit"]
    assert all(not c.actions for c in chunks[1:])


def test_llm_app_speaks_prose_when_the_model_ignores_the_json_format():
    raw = "Whales are the biggest animals on Earth. Some are as long as a bus!"
    chunks = list(_llm_app(_FakeStream(tokens=_tokens(raw))).respond_stream(_turn()))
    assert [c.text for c in chunks] == ["Whales are the biggest animals on Earth.",
                                        "Some are as long as a bus!"]


def test_llm_app_never_speaks_the_json_tail():
    raw = '{"say": "Just one line.", "mood": "positive", "gesture": "celebrate"}'
    chunks = list(_llm_app(_FakeStream(tokens=_tokens(raw, 3))).respond_stream(_turn()))
    assert [c.text for c in chunks] == ["Just one line."]
    assert "mood" not in chunks[0].text and "gesture" not in chunks[0].text


def test_llm_app_decodes_escapes_that_arrive_split_across_tokens():
    raw = '{"say": "She said \\"wow\\" and then 3.5 metres \\u2014 wild!"}'
    chunks = list(_llm_app(_FakeStream(tokens=_tokens(raw, 1))).respond_stream(_turn()))
    assert chunks[0].text == 'She said "wow" and then 3.5 metres — wild!'


def test_llm_app_falls_back_to_the_blocking_call_when_the_stream_fails():
    """A gateway that cannot stream must be no worse than before: one SUCCESS chunk with
    the ordinary answer, from the ordinary code path."""
    fake = _FakeStream(fail=RuntimeError("streaming not supported"),
                       whole='{"say": "Whales are enormous!", "mood": "surprised", '
                             '"gesture": "big"}')
    chunks = list(_llm_app(fake).respond_stream(_turn()))
    assert [(c.text, c.final) for c in chunks] == [("Whales are enormous!", True)]
    assert chunks[0].result_code == ResultCode.SUCCESS
    assert fake.stream_calls == 1 and fake.whole_calls == 1


def test_a_streaming_fallback_carries_the_offline_result_code():
    """When the fallback answer is ERROR_OFFLINE the closing chunk must be too — that
    result is what makes the robot switch to its on-device brain instead of hanging
    (docs/architecture/ai-seam.md §2)."""
    app = _llm_app(_FakeStream(fail=RuntimeError("no stream")))
    app.respond = lambda turn: Reply.offline()
    chunks = list(app.respond_stream(_turn()))
    assert [(c.result_code, c.final) for c in chunks] == [(ResultCode.ERROR_OFFLINE, True)]


def test_the_streamed_and_blocking_paths_agree_on_the_words():
    """Same completion, same persona, same tags — only the delivery differs."""
    raw = ('{"say": "<exit>Okay! Have a great night, Sam. Sleep well.", '
           '"mood": "positive", "gesture": "talk"}')
    streamed = list(_llm_app(_FakeStream(tokens=_tokens(raw))).respond_stream(_turn("bye")))
    whole = _llm_app(_FakeStream(whole=raw)).respond(_turn("bye"))
    assert " ".join(c.text for c in streamed) == whole.text
    assert [a.type for a in streamed[0].actions] == [a.type for a in whole.actions]
