"""
The robot's notify is the record, not a second copy.

A real Moxie reports what it said after each utterance: a `remote-chat` request with
`command: "notify"`, the child's line in `extra_lines[]` (`context_type: "input"`) and
Moxie's words in `speech` (mqtt-and-conversation.md §4.2; OpenMoxie
`site/hive/mqtt/conversations.py:59-68`). The runtime ALSO remembers each turn when it
answers (`MemoryMixin._remember`), the only writer a robot that never notifies has. Until
this file both wrote, so every exchange was held twice: one turn plus its notify left four
entries, the brain's window held half the real conversation, and the goodbye summary
(`ContentApp.on_session_end`) read a doubled transcript. Now a notify is reconciled with
the turn it reports (`memory.py`, "the robot's notify").

Through the REAL runtime (fake transport, fake brains, tmp storage) and, where the timing
matters, the REAL SIL robot (`sim/virtual_moxie.py`) on `helpers_runtime.loopback()`.
Hermetic: no broker, network or clock reads; waits are events and the worker pool.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import uuid

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if os.path.join(REPO, "sim") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "sim"))

from helpers_runtime import CHAT_TOPIC, LatchClient, fresh_pool, make_runtime  # noqa: E402
from moxie_sdk.app import MoxieApp                                           # noqa: E402
from moxie_sdk.filler import FILLERS                                         # noqa: E402
from moxie_sdk.store import JsonStore                                        # noqa: E402
from moxie_sdk.types import Reply, ReplyChunk                                # noqa: E402

PROMPT = "what is the moon made of?"
ANSWER = "Rock and dust!"
#: A streamed answer, one chunk per sentence (`MOXIE_STREAMING`, on by default).
SENTENCES = ("The moon is mostly rock.", "It is covered in grey dust.",
             "Some craters even hold ice!")
STREAMED = " ".join(SENTENCES)
#: A hello queued while a turn was in flight, said as chunk 0 of the next (`_speak_opener`).
HELLO = "Hey Sam, there you are! I missed you."
FILLER_TEXTS = [text for text, _markup in FILLERS]
#: Long enough that a loaded CI box never trips it, short enough that a hang fails.
PATIENCE = 10.0


class _Brain(MoxieApp):
    """`PROMPT` -> `ANSWER`; a line in `streamed` -> `SENTENCES` as a stream; a line in
    `answers` -> that answer, streamed one chunk per sentence when it is a tuple; anything
    else -> "Okay!". Records the history each call saw."""
    name = "notify-brain"

    def __init__(self, streamed=(), answers=None):
        self.streamed = set(streamed)
        self.answers = dict(answers or {})
        self.seen = []

    def respond(self, turn):
        self.seen.append(list(turn.history))
        if turn.speech == PROMPT:
            return Reply(text=ANSWER)
        answer = self.answers.get(turn.speech, "Okay!")
        return Reply(text=" ".join(answer) if isinstance(answer, tuple) else answer)

    def respond_stream(self, turn):
        chunks = SENTENCES if turn.speech in self.streamed else self.answers.get(turn.speech)
        if not isinstance(chunks, tuple):
            return None
        self.seen.append(list(turn.history))
        return (ReplyChunk(text=s, final=i == len(chunks) - 1) for i, s in enumerate(chunks))


def _runtime(tmp_path, app, **kw):
    rt, did = make_runtime(app, store=JsonStore(str(tmp_path / "data")), **kw)
    rt.client = LatchClient(runtime=rt)
    rt.streaming = True
    return rt, did


def _push(rt, did, speech, event_id=None, **fields):
    """One prompt, as the robot's `events/remote-chat` arrives; does not wait."""
    rt._on_remote_chat(did, rt.robots[did], json.dumps(
        {"command": "prompt", "backend": "router", "event_id": event_id or str(uuid.uuid4()),
         "speech": speech, **fields}))


def _ask(rt, did, speech, event_id=None, **fields):
    """One whole turn: pushed, answered and remembered (the pool drained, then re-armed)."""
    _push(rt, did, speech, event_id, **fields)
    rt._pool.shutdown(wait=True)
    fresh_pool(rt)


def _in(text):
    """One child line as a notify (or a prompt) carries it in `extra_lines`."""
    return {"context_type": "input", "text": text}


def _notify_raw(rt, did, **fields):
    """A notify with exactly these fields (a malformed one included), through
    `_on_remote_chat` as `_on_message` hands it on."""
    rt._on_remote_chat(did, rt.robots[did], json.dumps(
        {"command": "notify", "backend": "router", "event_id": str(uuid.uuid4()), **fields}))


def _notify(rt, did, speech=None, said=None):
    """The contract's notify (mqtt-and-conversation.md §4.2) through `_on_remote_chat`."""
    fields = {}
    if speech is not None:
        fields["speech"] = speech
    if said is not None:
        fields["extra_lines"] = [_in(said)]
    _notify_raw(rt, did, **fields)


def _published(rt, did, n, how_many=1):
    """Wait until `how_many` more replies than `n` have been published to this robot."""
    topic = CHAT_TOPIC.format(device_id=did)
    assert rt.client.wait_for(
        lambda pubs: sum(t == topic for t, _ in pubs) >= n + how_many, PATIENCE), \
        "the runtime published nothing in time"


def _lines(rt, did):
    return [(m["role"], m["content"]) for m in rt.history.get(did, [])]


def _said(rt, did):
    return [r["output"]["text"] for r in rt.client.on(CHAT_TOPIC.format(device_id=did))]


def _on_disk(memdir, did):
    with open(os.path.join(memdir, f"{did}.json")) as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# One turn and the robot's report of it
# --------------------------------------------------------------------------- #
def test_the_robots_notify_does_not_duplicate_the_turn_it_echoes(tmp_path, monkeypatch):
    """The contract's notify for the turn just answered changes nothing, in RAM or on
    disk. origin/dev: four entries, each line twice."""
    memdir = tmp_path / "memory"
    monkeypatch.setenv("MOXIE_MEMORY_DIR", str(memdir))
    rt, did = _runtime(tmp_path, _Brain())
    _ask(rt, did, PROMPT)
    _notify(rt, did, ANSWER, said=PROMPT)
    want = [{"role": "user", "content": PROMPT}, {"role": "assistant", "content": ANSWER}]
    assert rt.history[did] == want
    assert json.loads(_on_disk(memdir, did)) == want


@pytest.mark.parametrize("reports", [
    [STREAMED],
    list(SENTENCES),
    [SENTENCES[1], SENTENCES[0], SENTENCES[2]],
], ids=["one-notify-per-turn", "one-per-sentence", "one-per-sentence-out-of-order"])
def test_a_streamed_answer_and_its_notifies_leave_one_line(tmp_path, reports):
    """Per turn or per chunk (unknown on a real robot), in any order, every report
    carrying the child's line (the worst case): the turn ends as the child's line and
    one Moxie line holding all three sentences in order."""
    rt, did = _runtime(tmp_path, _Brain(streamed={PROMPT}))
    _ask(rt, did, PROMPT)
    assert _said(rt, did) == list(SENTENCES)
    for spoken in reports:
        _notify(rt, did, spoken, said=PROMPT)
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", STREAMED)]


def test_a_tail_piece_or_a_report_with_a_hole_never_drops_the_head(tmp_path):
    """A robot that reports only its last chunk per event, or whose middle chunk's report
    never arrives: the words it reported are marked, the entry keeps the whole text (a
    head the robot may still report is never dropped). Only a clean cut shortens it."""
    rt, did = _runtime(tmp_path, _Brain(streamed={PROMPT}))
    _ask(rt, did, PROMPT)
    whole = [("user", PROMPT), ("assistant", STREAMED)]
    _notify(rt, did, SENTENCES[2], said=PROMPT)                 # the tail alone
    assert _lines(rt, did) == whole
    _notify(rt, did, SENTENCES[0])                              # head and tail: a hole
    assert _lines(rt, did) == whole
    _notify(rt, did, SENTENCES[1])                              # the hole filled
    assert _lines(rt, did) == whole


class _Cheer(MoxieApp):
    """Streams the same sentence three times."""
    name = "cheer"

    def respond(self, turn):
        return Reply(text="Okay!")

    def respond_stream(self, turn):
        return (ReplyChunk(text="Yes!", final=i == 2) for i in range(3))


def test_an_answer_that_repeats_a_sentence_reported_chunk_by_chunk_keeps_every_repeat(
        tmp_path):
    """"Yes! Yes! Yes!" streamed as three chunks and reported one at a time: each report
    marks the next run not yet reported, so the entry is never cut down to one "Yes!"
    (a clean cut after the first report is what the robot has said so far)."""
    rt, did = _runtime(tmp_path, _Cheer())
    _ask(rt, did, PROMPT)
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", "Yes! Yes! Yes!")]
    _notify(rt, did, "Yes!", said=PROMPT)
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", "Yes!")]
    _notify(rt, did, "Yes!")
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", "Yes! Yes!")]
    _notify(rt, did, "Yes!")
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", "Yes! Yes! Yes!")]


def test_a_report_carrying_markup_or_other_casing_is_still_the_turns_line(tmp_path):
    """What the robot renders may differ from the text we remember in case, punctuation
    or inline marks; compared word by word with tags out, it is the same line."""
    rt, did = _runtime(tmp_path, _Brain())
    _ask(rt, did, PROMPT)
    _notify(rt, did, '<mark name="cmd:playback,data:{}"/> rock AND dust', said=PROMPT.upper())
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", ANSWER)]


def test_a_turn_remembered_without_a_start_still_reconciles_its_notify(tmp_path):
    """`_remember` called with no `_on_remote_chat` before it (a direct call) completes a
    record of its own, so the notify that follows is still not a second copy."""
    rt, did = _runtime(tmp_path, _Brain())
    rt._remember(did, PROMPT, ANSWER)
    _notify(rt, did, ANSWER, said=PROMPT)
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", ANSWER)]


def test_per_chunk_notifies_sent_while_the_stream_is_still_open_are_held_for_it(tmp_path):
    """The SIL robot reporting each chunk as it lands: every notify reaches the runtime
    BEFORE the stream closes and `_remember` runs (the order is recorded, not assumed),
    and the turn still ends as one line each."""
    pytest.importorskip("paho.mqtt.client", reason="the SIL robot needs paho")
    from helpers_runtime import loopback
    from virtual_moxie import VirtualMoxie
    rt, did = _runtime(tmp_path, _Brain(streamed={PROMPT}))
    order = []
    remember, ingest = rt._remember, rt._ingest_notify
    rt._remember = lambda *a, **k: (order.append("remember"), remember(*a, **k))[1]
    rt._ingest_notify = lambda *a, **k: (order.append("notify"), ingest(*a, **k))[1]
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id=did, verbose=False,
                      notify="chunk")
    loopback(rt, vm)
    vm.send_prompt(PROMPT)
    rt._pool.shutdown(wait=True)
    assert [n["speech"] for n in vm.notified] == list(SENTENCES)
    assert order == ["notify", "notify", "notify", "remember"], order
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", STREAMED)]


# --------------------------------------------------------------------------- #
# What the runtime says around the answer: a filler, a queued hello
# --------------------------------------------------------------------------- #
class _SlowBrain(MoxieApp):
    """Answers only when the test lets it, so a filler goes out first. No sleeps."""
    name = "slow"

    def __init__(self):
        self.release = threading.Event()

    def respond(self, turn):
        assert self.release.wait(PATIENCE), "the test never released the slow brain"
        return Reply(text=ANSWER)


@pytest.mark.parametrize("joined", [False, True], ids=["one-per-chunk", "one-per-turn"])
def test_a_filler_echoed_by_a_notify_is_never_history(tmp_path, joined):
    """A slow brain: the filler goes out as chunk 0 and the robot reports it while the
    brain is still thinking. It never enters history, and it is never left standing as
    the turn's only Moxie line; the answer then lands once."""
    app = _SlowBrain()
    rt, did = _runtime(tmp_path, app)
    rt.brain_budget_s = 0.05
    _push(rt, did, PROMPT)
    topic = CHAT_TOPIC.format(device_id=did)
    assert rt.client.wait_for(lambda pubs: any(t == topic for t, _ in pubs), PATIENCE)
    filler = _said(rt, did)[0]
    assert filler in FILLER_TEXTS
    if not joined:
        _notify(rt, did, filler, said=PROMPT)
        assert _lines(rt, did) == [], "the filler (or the child's line) went in early"
    app.release.set()
    rt._pool.shutdown(wait=True)
    assert _said(rt, did) == [filler, ANSWER]
    if joined:
        _notify(rt, did, f"{filler} {ANSWER}", said=PROMPT)
    else:
        _notify(rt, did, ANSWER)
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", ANSWER)]


def test_a_queued_hello_echoed_by_a_notify_is_never_added(tmp_path):
    """The hello rides out as chunk 0 of a plain answer; reported per chunk or with the
    answer, it adds nothing."""
    rt, did = _runtime(tmp_path, _Brain())
    rt._pending_opener[did] = HELLO
    _ask(rt, did, PROMPT)
    assert _said(rt, did) == [HELLO, ANSWER]
    _notify(rt, did, HELLO, said=PROMPT)
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", ANSWER)]
    _notify(rt, did, f"{HELLO} {ANSWER}")
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", ANSWER)]


def test_a_streamed_hello_reported_alone_never_becomes_the_turns_only_line(tmp_path):
    """On the streamed path the hello opens the turn's own remembered line (as it always
    has); the robot reporting it first, then each sentence, leaves exactly that line."""
    rt, did = _runtime(tmp_path, _Brain(streamed={PROMPT}))
    rt._pending_opener[did] = HELLO
    _ask(rt, did, PROMPT)
    remembered = [("user", PROMPT), ("assistant", f"{HELLO} {STREAMED}")]
    assert _lines(rt, did) == remembered
    _notify(rt, did, HELLO, said=PROMPT)
    assert _lines(rt, did) == remembered
    for spoken in SENTENCES:
        _notify(rt, did, spoken)
    assert _lines(rt, did) == remembered


@pytest.mark.parametrize("spoken_first", [None, FILLER_TEXTS[1], FILLER_TEXTS[0]],
                         ids=["answer-alone", "after-another-filler", "after-the-same-filler"])
def test_an_answer_that_says_a_fillers_words_is_still_one_line(tmp_path, spoken_first):
    """The brain's own answer holds a filler's words mid-sentence and the robot reports
    it word for word: those words are the turn's text, not a filler, so the report is the
    turn's line. (Stripped of them, the report was no longer a run in the text and went in
    as a second Moxie line.) A filler the robot really spoke before the answer, reported
    in the same notify, is still never history, even when it is the one the answer says."""
    answer = f"The moon? {FILLER_TEXTS[0]} It is mostly rock."
    rt, did = _runtime(tmp_path, _Brain(answers={"tell me": answer}))
    _ask(rt, did, "tell me")
    want = [("user", "tell me"), ("assistant", answer)]
    assert _lines(rt, did) == want
    _notify(rt, did, f"{spoken_first} {answer}" if spoken_first else answer, said="tell me")
    assert _lines(rt, did) == want


# --------------------------------------------------------------------------- #
# Moxie is authoritative about what it said
# --------------------------------------------------------------------------- #
def test_a_child_who_interrupts_leaves_what_moxie_actually_said(tmp_path, monkeypatch):
    """A notify reading "Rock and" after the turn closed: the child cut Moxie off, and
    the entry becomes what was said, in RAM and on disk."""
    memdir = tmp_path / "memory"
    monkeypatch.setenv("MOXIE_MEMORY_DIR", str(memdir))
    rt, did = _runtime(tmp_path, _Brain())
    _ask(rt, did, PROMPT)
    _notify(rt, did, "Rock and", said=PROMPT)
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", "Rock and")]
    assert json.loads(_on_disk(memdir, did)) == [
        {"role": "user", "content": PROMPT}, {"role": "assistant", "content": "Rock and"}]


def test_an_interrupted_stream_keeps_the_sentences_moxie_got_through(tmp_path):
    rt, did = _runtime(tmp_path, _Brain(streamed={PROMPT}))
    _ask(rt, did, PROMPT)
    _notify(rt, did, SENTENCES[0], said=PROMPT)
    _notify(rt, did, "It is covered")
    assert _lines(rt, did) == [("user", PROMPT),
                               ("assistant", f"{SENTENCES[0]} It is covered")]


def test_a_line_the_runtime_never_sent_is_appended_and_the_next_one_joins_it(tmp_path):
    """A module's own line (no turn to match): appended; a second in a row joins it, as
    OpenMoxie's `add_history` does (conversations.py:29-39). After a turn, the module's
    lines follow the answer and never join the turn's own entry."""
    rt, did = _runtime(tmp_path, _Brain())
    _notify(rt, did, "Welcome to the dance party!")
    assert _lines(rt, did) == [("assistant", "Welcome to the dance party!")]
    _notify(rt, did, "Let's warm up.")
    assert _lines(rt, did) == [("assistant", "Welcome to the dance party! Let's warm up.")]
    _ask(rt, did, PROMPT)
    _notify(rt, did, ANSWER, said=PROMPT)
    _notify(rt, did, "Want to hear more?")
    _notify(rt, did, "Just say yes!")
    assert _lines(rt, did) == [
        ("assistant", "Welcome to the dance party! Let's warm up."),
        ("user", PROMPT), ("assistant", ANSWER),
        ("assistant", "Want to hear more? Just say yes!")]


def test_a_report_with_the_answer_and_a_line_of_its_own_keeps_both_once(tmp_path):
    rt, did = _runtime(tmp_path, _Brain())
    _ask(rt, did, PROMPT)
    _notify(rt, did, f"{ANSWER} Want to hear more?", said=PROMPT)
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", ANSWER),
                               ("assistant", "Want to hear more?")]


def test_animation_and_silent_lines_are_still_dropped(tmp_path):
    rt, did = _runtime(tmp_path, _Brain())
    _ask(rt, did, PROMPT)
    _notify(rt, did, f"animation:Bht_Wave\n{ANSWER}\nsilent:Bht_Nod", said=PROMPT)
    _notify(rt, did, "animation:Bht_Celebrate")
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", ANSWER)]


def test_a_line_the_child_really_said_twice_is_kept_twice(tmp_path):
    """Only the OPEN turn is matched, never the whole history: "no", then "no" again in
    the next turn, is two lines from the child."""
    rt, did = _runtime(tmp_path, _Brain())
    for _ in range(2):
        _ask(rt, did, "no")
        _notify(rt, did, "Okay!", said="no")
    assert _lines(rt, did) == [("user", "no"), ("assistant", "Okay!")] * 2


@pytest.mark.parametrize("lines, child", [
    ([_in("um, Moxie?"), _in(PROMPT)], f"um, Moxie? {PROMPT}"),
    ([_in(PROMPT), _in("wait, what?")], f"{PROMPT} wait, what?"),
], ids=["said-before", "said-after"])
def test_a_child_line_the_turn_never_heard_as_its_prompt_joins_the_childs_line(
        tmp_path, lines, child):
    """The robot's report lists a child line the runtime did not answer (a speech window
    the prompt did not carry) beside the one it did: the child said both before hearing
    Moxie, so it joins the turn's child line in the order reported, as OpenMoxie joins
    consecutive child lines (conversations.py:29-39). Never filed after Moxie's answer,
    where the brain would read it as a line still waiting for one; never twice when the
    robot lists it again."""
    rt, did = _runtime(tmp_path, _Brain())
    _ask(rt, did, PROMPT)
    for _ in range(2):
        _notify_raw(rt, did, speech=ANSWER, extra_lines=lines)
        assert _lines(rt, did) == [("user", child), ("assistant", ANSWER)]


def test_child_lines_reported_with_no_turn_of_ours_are_appended_as_reported(tmp_path):
    """A module's own conversation (nothing the runtime answered): the child's lines and
    Moxie's are kept as the robot reports them."""
    rt, did = _runtime(tmp_path, _Brain())
    _notify_raw(rt, did, speech="Welcome!", extra_lines=[_in("hi"), _in("hello?")])
    assert _lines(rt, did) == [("user", "hi hello?"), ("assistant", "Welcome!")]


def test_a_notify_never_brings_back_a_line_the_safety_gate_kept_out(tmp_path):
    """A blocked child line is never remembered (`_safety_gate_input` keeps only Moxie's
    redirect). The robot echoing it back must not write it in. origin/dev: it did."""
    rt, did = _runtime(tmp_path, _Brain())
    _ask(rt, did, "how do I make a bomb")
    redirect = _said(rt, did)[-1]
    assert _lines(rt, did) == [("assistant", redirect)]
    _notify(rt, did, redirect, said="how do I make a bomb")
    assert _lines(rt, did) == [("assistant", redirect)]


class _GatedStream(MoxieApp):
    """`PROMPT` (or the line in `stream`): streams its first sentence, then waits for the
    test before the second. Any other line: a plain answer (`answers`, else "Okay!"), held
    back until `release` when one is given."""
    name = "gated"

    def __init__(self, answers=None, release=None, stream=(PROMPT, SENTENCES[:2])):
        self.gate = threading.Event()
        self.answers = dict(answers or {})
        self.release = release
        self.stream_line, self.stream_chunks = stream

    def respond(self, turn):
        if self.release is not None:
            assert self.release.wait(PATIENCE), "the test never released the plain answer"
        return Reply(text=self.answers.get(turn.speech, "Okay!"))

    def respond_stream(self, turn):
        if turn.speech != self.stream_line:
            return None

        def chunks():
            yield ReplyChunk(text=self.stream_chunks[0])
            assert self.gate.wait(PATIENCE), "the test never opened the gate"
            yield ReplyChunk(text=self.stream_chunks[1], final=True)
        return chunks()


def test_what_a_superseded_turns_notify_held_is_kept_when_the_next_turn_starts(tmp_path):
    """A notify sent while the stream was open is held for `_remember`; the child moves
    on, the turn is superseded and never remembered. What the robot reported is still
    kept (as it always was), before the new turn's lines."""
    app = _GatedStream()
    rt, did = _runtime(tmp_path, app)
    _push(rt, did, PROMPT)
    topic = CHAT_TOPIC.format(device_id=did)
    assert rt.client.wait_for(lambda pubs: any(t == topic for t, _ in pubs), PATIENCE)
    _notify(rt, did, SENTENCES[0], said=PROMPT)
    assert _lines(rt, did) == []                   # held: the turn is still open
    _push(rt, did, "never mind")
    # Kept by the new turn's start (this thread); its own lines may already follow.
    assert _lines(rt, did)[:2] == [("user", PROMPT), ("assistant", SENTENCES[0])]
    app.gate.set()
    rt._pool.shutdown(wait=True)
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", SENTENCES[0]),
                               ("user", "never mind"), ("assistant", "Okay!")]


def test_a_superseded_worker_past_its_stale_check_leaves_the_next_turns_record_open(tmp_path):
    """The child's next prompt lands between an old worker's stale check and its
    `_remember`: the old turn's lines are written as they always were, but the record now
    open belongs to the new turn, so the reports arriving before the new turn closes (the
    old turn's, if the robot spoke it, then the new turn's first chunk) are held for it
    rather than appended beside it, and replayed in order: each names its own turn."""
    rt, did = _runtime(tmp_path, _Brain())
    rt._start_turn_record(did, "first")
    rt._start_turn_record(did, "second")                  # the child moved on
    rt._remember(did, "first", "One.")                    # the old worker, past its check
    _notify(rt, did, "One.", said="first")                # spoken first, reported first
    _notify(rt, did, "Two.", said="second")               # the new turn, still open
    assert _lines(rt, did) == [("user", "first"), ("assistant", "One.")]
    rt._remember(did, "second", "Two.")
    assert _lines(rt, did) == [("user", "first"), ("assistant", "One."),
                               ("user", "second"), ("assistant", "Two.")]


def test_a_chunk_reported_before_the_stream_closed_cuts_the_entry_until_the_rest_is_reported(
        tmp_path):
    """The robot reports chunk 0 while the stream is still open: held, then applied as the
    turn is remembered, so the entry is what the robot has said so far (a clean cut), and
    the whole text once the rest is reported."""
    app = _GatedStream()
    rt, did = _runtime(tmp_path, app)
    _push(rt, did, PROMPT)
    _published(rt, did, 0)
    _notify(rt, did, SENTENCES[0], said=PROMPT)
    app.gate.set()
    rt._pool.shutdown(wait=True)
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", SENTENCES[0])]
    _notify(rt, did, SENTENCES[1])
    assert _lines(rt, did) == [("user", PROMPT),
                               ("assistant", f"{SENTENCES[0]} {SENTENCES[1]}")]


def test_what_a_superseded_turn_reported_reaches_the_disk_as_the_next_turn_starts(
        tmp_path, monkeypatch):
    """Kept in RAM by the next turn's start (above) and saved right then, not left for that
    turn's own save, which is a whole brain call away."""
    memdir = tmp_path / "memory"
    monkeypatch.setenv("MOXIE_MEMORY_DIR", str(memdir))
    app = _GatedStream(release=threading.Event())
    rt, did = _runtime(tmp_path, app)
    rt.brain_budget_s = 0
    _push(rt, did, PROMPT)
    _published(rt, did, 0)
    _notify(rt, did, SENTENCES[0], said=PROMPT)
    _push(rt, did, "never mind")                   # its plain answer waits on `release`
    assert json.loads(_on_disk(memdir, did)) == [
        {"role": "user", "content": PROMPT}, {"role": "assistant", "content": SENTENCES[0]}]
    app.release.set()
    app.gate.set()
    rt._pool.shutdown(wait=True)
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", SENTENCES[0]),
                               ("user", "never mind"), ("assistant", "Okay!")]


def test_a_superseded_turns_own_hello_reported_is_still_never_history(tmp_path):
    """The hello the runtime spoke as the superseded turn's chunk 0, reported by the robot
    and held: dropped with that turn's record as the next turn starts, while the sentence
    the robot got through is kept as reported."""
    app = _GatedStream()
    rt, did = _runtime(tmp_path, app)
    rt._pending_opener[did] = HELLO
    _push(rt, did, PROMPT)
    _published(rt, did, 0, how_many=2)
    assert _said(rt, did) == [HELLO, SENTENCES[0]]
    _notify(rt, did, HELLO, said=PROMPT)
    _notify(rt, did, SENTENCES[0])
    _push(rt, did, "never mind")
    assert _lines(rt, did)[:2] == [("user", PROMPT), ("assistant", SENTENCES[0])]
    app.gate.set()
    rt._pool.shutdown(wait=True)
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", SENTENCES[0]),
                               ("user", "never mind"), ("assistant", "Okay!")]


# --------------------------------------------------------------------------- #
# Several speech windows, one report
# --------------------------------------------------------------------------- #
#: Two of the child's speech windows, each answered as its own turn (the robot sent two
#: prompts), then one report naming both: OpenMoxie's reading of the notify, which is what
#: keeps its context right "even when the user provides input in multiple speech windows
#: before hearing a response" (moxie_remote_chat.py:8-11; conversations.py:63-65 adds
#: every input line).
WINDOWS = {"P one": "A one.", "P two": "A two."}
BOTH = [("user", "P one"), ("assistant", "A one."), ("user", "P two"), ("assistant", "A two.")]


def test_a_report_naming_the_windows_of_turns_already_answered_adds_no_line(tmp_path):
    """Each window's line is already held by the turn that answered it, so the report adds
    nothing. (Matched against the current turn alone, the earlier window went in again,
    after Moxie's second answer.)"""
    rt, did = _runtime(tmp_path, _Brain(answers=WINDOWS))
    _ask(rt, did, "P one")
    _ask(rt, did, "P two")
    assert _lines(rt, did) == BOTH
    _notify_raw(rt, did, speech="A two.", extra_lines=[_in("P one"), _in("P two")])
    assert _lines(rt, did) == BOTH


def test_a_report_joining_both_answers_marks_both_turns(tmp_path):
    """A robot that spoke both answers as one utterance: each turn's text is found in the
    report and marked, nothing is appended."""
    rt, did = _runtime(tmp_path, _Brain(answers=WINDOWS))
    _ask(rt, did, "P one")
    _ask(rt, did, "P two")
    _notify_raw(rt, did, speech="A one. A two.", extra_lines=[_in("P one"), _in("P two")])
    assert _lines(rt, did) == BOTH


def test_the_earlier_window_of_one_prompt_is_joined_before_the_childs_line(tmp_path):
    """One prompt carrying both windows (`_on_remote_chat` answers the last): the report
    lists both, and the earlier one joins the child's line before it, as OpenMoxie joins
    consecutive child lines, never filed after Moxie's answer. A robot that lists its
    windows again (per chunk) joins nothing twice."""
    rt, did = _runtime(tmp_path, _Brain(answers=WINDOWS))
    _ask(rt, did, "P two", extra_lines=[_in("P one"), _in("P two")])
    assert _lines(rt, did) == [("user", "P two"), ("assistant", "A two.")]
    for _ in range(2):
        _notify_raw(rt, did, speech="A two.", extra_lines=[_in("P one"), _in("P two")])
        assert _lines(rt, did) == [("user", "P one P two"), ("assistant", "A two.")]


def test_the_windows_of_turns_already_answered_listed_on_every_chunk_add_nothing(tmp_path):
    """"P one" answered as its own turn and not reported; "P two" answered in two chunks,
    each reported with both windows. The first report holds "P one" already and retires
    that turn; the second lists it again, and it was listed with the turn it names, so it
    is not joined as a new window. (Red on the round-3 head: its turn retired, the
    re-listed "P one" joined "P two" on the second chunk.)"""
    rt, did = _runtime(tmp_path, _Brain(answers={"P one": "A one.",
                                                 "P two": ("A two.", "And more.")}))
    _ask(rt, did, "P one")
    _ask(rt, did, "P two")
    windows = [_in("P one"), _in("P two")]
    _notify_raw(rt, did, speech="A two.", extra_lines=windows)
    _notify_raw(rt, did, speech="And more.", extra_lines=windows)
    assert _lines(rt, did) == [("user", "P one"), ("assistant", "A one."),
                               ("user", "P two"), ("assistant", "A two. And more.")]


def test_a_window_said_again_with_the_next_prompt_is_a_new_line(tmp_path):
    """The child opens each question with "um", a window of its own: two prompts in a row
    carry ["um", question], each reported by its own notify. The second "um" joins the
    second turn's line as the first did the first's. (Red on the round-3 head: a window
    joined to ANY turn not yet retired counted as listed again, and the first turn is
    retired only by the report that names the second.)"""
    answers = {"what is a star?": "A ball of hot gas.", "and the sun?": "The sun is a star!"}
    rt, did = _runtime(tmp_path, _Brain(answers=answers))
    for question, answer in answers.items():
        windows = [_in("um"), _in(question)]
        _ask(rt, did, question, extra_lines=windows)
        _notify_raw(rt, did, speech=answer, extra_lines=windows)
    assert _lines(rt, did) == [("user", "um what is a star?"), ("assistant", "A ball of hot gas."),
                               ("user", "um and the sun?"), ("assistant", "The sun is a star!")]


def test_the_previous_turns_line_said_again_as_a_window_is_a_new_line_joined_once(tmp_path):
    """"Moxie?" is answered and reported on its own; the child says "Moxie?" again before
    the next question, both windows reported with each chunk of that answer. The robot
    reported the first turn already, so its line listed again before the new turn's is
    the child saying it again (OpenMoxie appends every input line of every notify,
    conversations.py:63-65), joined before "tell me a joke", and once: the second chunk
    lists it a third time. When the first turn was answered but NOT yet reported, the
    same report adds nothing (`..._windows_of_turns_already_answered_adds_no_line`).
    (Red on the round-3 head: the second "Moxie?" was dropped as the first turn's line,
    that turn being retired only by this report.)"""
    rt, did = _runtime(tmp_path, _Brain(answers={
        "Moxie?": "Yes? I am here.",
        "tell me a joke": ("Why did the cow cross?", "To get to the other side.")}))
    _ask(rt, did, "Moxie?")
    _notify(rt, did, "Yes? I am here.", said="Moxie?")
    windows = [_in("Moxie?"), _in("tell me a joke")]
    _ask(rt, did, "tell me a joke", extra_lines=windows)
    first = [("user", "Moxie?"), ("assistant", "Yes? I am here.")]
    _notify_raw(rt, did, speech="Why did the cow cross?", extra_lines=windows)
    assert _lines(rt, did) == first + [("user", "Moxie? tell me a joke"),
                                       ("assistant", "Why did the cow cross?")]
    _notify_raw(rt, did, speech="To get to the other side.", extra_lines=windows)
    assert _lines(rt, did) == first + [("user", "Moxie? tell me a joke"),
                                       ("assistant", "Why did the cow cross? To get to the other side.")]


def test_a_completed_turns_report_landing_after_the_next_prompt_adds_nothing(tmp_path):
    """The robot reports the first turn only after the child's next prompt has opened the
    second (a slow report; a child who barges in): held for the open turn, then matched to
    the turn it names, not appended beside the new one."""
    app = _GatedStream(answers=WINDOWS)
    rt, did = _runtime(tmp_path, app)
    _ask(rt, did, "P one")
    first = [("user", "P one"), ("assistant", "A one.")]
    assert _lines(rt, did) == first
    _push(rt, did, PROMPT)                          # streamed: open after chunk 0
    _published(rt, did, 1)
    _notify(rt, did, "A one.", said="P one")        # the late report, held
    assert _lines(rt, did) == first
    app.gate.set()
    rt._pool.shutdown(wait=True)
    assert _lines(rt, did) == first + [("user", PROMPT),
                                       ("assistant", f"{SENTENCES[0]} {SENTENCES[1]}")]


def test_each_turns_own_report_consumes_its_record_so_a_real_repeat_stays_two_lines(
        tmp_path):
    """Both reports arrive late, after both turns were answered: the first names the first
    "no" and retires nothing, the second names the second; "Okay!" twice stays two lines,
    not one turn marked twice and the other appended."""
    rt, did = _runtime(tmp_path, _Brain())
    _ask(rt, did, "no")
    _ask(rt, did, "no")
    for _ in range(2):
        _notify(rt, did, "Okay!", said="no")
    assert _lines(rt, did) == [("user", "no"), ("assistant", "Okay!")] * 2


@pytest.mark.parametrize("spoken, tail", [
    ("Okay!", []),
    ("Welcome to the dance party!", [("assistant", "Welcome to the dance party!")]),
], ids=["the-turns-answer", "a-line-of-the-robots-own"])
def test_a_window_joins_the_turn_its_report_names_not_an_earlier_one_already_reported(
        tmp_path, spoken, tail):
    """Two turns with the same child line, both reported late: the second report's extra
    window joins the second turn's line, in the order reported, whether its speech is
    that turn's answer (which fits the second turn, the first being reported) or a line of
    the robot's own that fits neither (the first turn is reported in full, so the line
    names the second)."""
    rt, did = _runtime(tmp_path, _Brain())
    _ask(rt, did, "no")
    _ask(rt, did, "no")
    _notify(rt, did, "Okay!", said="no")
    _notify_raw(rt, did, speech=spoken, extra_lines=[_in("wait"), _in("no")])
    assert _lines(rt, did) == [("user", "no"), ("assistant", "Okay!"),
                               ("user", "wait no"), ("assistant", "Okay!")] + tail


def test_a_turn_the_robot_has_reported_past_no_longer_absorbs_a_new_line(tmp_path):
    """Once the robot reports a later turn, every earlier one is dropped from the records,
    so the child saying the same words again where no turn of ours answers (a module's
    own conversation) is a new line, not one already held."""
    rt, did = _runtime(tmp_path, _Brain(answers={"yes": "Sure!"}))
    _ask(rt, did, "no")
    _notify(rt, did, "Okay!", said="no")
    _ask(rt, did, "yes")
    _notify(rt, did, "Sure!", said="yes")
    _notify_raw(rt, did, speech="Welcome to the dance party!", extra_lines=[_in("no")])
    assert _lines(rt, did) == [("user", "no"), ("assistant", "Okay!"),
                               ("user", "yes"), ("assistant", "Sure!"),
                               ("user", "no"), ("assistant", "Welcome to the dance party!")]


def test_a_robot_that_never_notifies_keeps_a_bounded_set_of_records(tmp_path):
    """The records wait for reports that never come: `UNREPORTED_TURNS` of them at most,
    while history itself is untouched."""
    from moxie_runtime.memory import UNREPORTED_TURNS
    rt, did = _runtime(tmp_path, _Brain())
    turns = UNREPORTED_TURNS * 2 + 1
    for n in range(turns):
        _ask(rt, did, f"line {n}")
    assert len(rt._turn_records()[did]) == UNREPORTED_TURNS
    assert len(rt.history[did]) == 2 * turns


# --------------------------------------------------------------------------- #
# A turn the robot has reported past is never matched again
# --------------------------------------------------------------------------- #
#: The robot speaks in order: its report of a turn means every older turn is over, whether
#: reported in full, in part (the child cut in) or never (a reply the robot skipped). Two
#: answers ending with the same question (a brain's habit), streamed a sentence at a time,
#: the first cut off by the child after its first sentence; and one scripted answer the
#: child hears twice, cut off the first time.
MOON = ("The moon is made of rock.", "What do you think?")
STARS = ("Stars are hot gas.", "What do you think?")
ENDINGS = {"tell me about the moon": MOON, "and the stars?": STARS}
GAME = ("Okay!", "Let's play a game.")


@pytest.mark.parametrize("per_chunk", [True, False], ids=["one-per-chunk", "one-per-turn"])
def test_a_later_turns_report_never_lands_on_the_turn_the_child_cut_short(tmp_path,
                                                                           per_chunk):
    """The child cuts the moon answer off after its first sentence (the robot reports that
    much), then asks about the stars and hears the whole answer, which ends with the same
    question. The report of the stars turn retires the moon turn, so the question is the
    stars turn's, reported per chunk or per turn. (Before, per chunk: the moon turn, never
    reported in full and so never retired, took the question as its own unsaid tail, the
    cut was undone, and the stars turn was left cut instead.)"""
    rt, did = _runtime(tmp_path, _Brain(answers=ENDINGS))
    _ask(rt, did, "tell me about the moon")
    _notify(rt, did, MOON[0], said="tell me about the moon")        # the child cut in
    _ask(rt, did, "and the stars?")
    if per_chunk:
        _notify(rt, did, STARS[0], said="and the stars?")
        _notify(rt, did, STARS[1])
    else:
        _notify(rt, did, " ".join(STARS), said="and the stars?")
    assert _lines(rt, did) == [("user", "tell me about the moon"), ("assistant", MOON[0]),
                               ("user", "and the stars?"), ("assistant", " ".join(STARS))]


def test_a_cut_off_turn_is_retired_by_the_next_report_like_any_older_turn(tmp_path):
    """A cut-off turn is never reported in full. It is dropped from the records the moment
    the robot reports a later turn, as every older turn is, so twelve turns on it cannot
    absorb a repeat of its unsaid words, and the brain's next prompt reads the cut as it
    was. (Before: only turns reported in full were retired, the bounded list never filled,
    and a cut-off turn lingered for the whole session.)"""
    app = _Brain(answers=ENDINGS)
    rt, did = _runtime(tmp_path, app)
    _ask(rt, did, "tell me about the moon")
    _notify(rt, did, MOON[0], said="tell me about the moon")
    for n in range(12):
        _ask(rt, did, f"line {n}")
        _notify(rt, did, "Okay!", said=f"line {n}")
        assert len(rt._turn_records()[did]) == 1, "the turn just reported, and no other"
    _ask(rt, did, "and the stars?")
    _notify(rt, did, STARS[0], said="and the stars?")
    _notify(rt, did, STARS[1])
    _ask(rt, did, "bye")
    read = [(m["role"], m["content"]) for m in app.seen[-1]]
    assert read == _lines(rt, did)[:-2]
    assert read[1] == ("assistant", MOON[0])
    assert read[-1] == ("assistant", " ".join(STARS))


def test_the_childs_repeat_of_a_cut_off_turns_line_is_a_new_line(tmp_path):
    """The child asks about the moon and cuts Moxie off at "Rock and"; says "cool"; then
    asks again in two speech windows, "what is the moon made of?" and "tell me more",
    reported together. The first window is a new line, joined before the child's line, as
    it is when the first turn was reported in full. (Before: dropped as a line the cut-off
    turn already held, so the repeated question was gone from history.)"""
    rt, did = _runtime(tmp_path, _Brain(answers={"tell me more": "More moon facts!"}))
    _ask(rt, did, PROMPT)
    _notify(rt, did, "Rock and", said=PROMPT)
    _ask(rt, did, "cool")
    _notify(rt, did, "Okay!", said="cool")
    windows = [_in(PROMPT), _in("tell me more")]
    _ask(rt, did, "tell me more", extra_lines=windows)
    _notify_raw(rt, did, speech="More moon facts!", extra_lines=windows)
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", "Rock and"),
                               ("user", "cool"), ("assistant", "Okay!"),
                               ("user", f"{PROMPT} tell me more"),
                               ("assistant", "More moon facts!")]


def test_a_turn_the_robot_never_reported_is_retired_by_a_later_report_too(tmp_path):
    """"Moxie?" is answered but never reported (the robot skipped that reply); ten reported
    turns follow; then the child says "Moxie?" again as the earlier window of a prompt. The
    first report of a later turn retired the unreported turn, so the window is a new line,
    joined before the child's line. (Before: it lingered, and the repeat was dropped as a
    line already held.)"""
    rt, did = _runtime(tmp_path, _Brain(answers={"Moxie?": "Yes? I am here.",
                                                 "tell me a joke": "Why did the cow cross?"}))
    _ask(rt, did, "Moxie?")
    for n in range(10):
        _ask(rt, did, f"line {n}")
        _notify(rt, did, "Okay!", said=f"line {n}")
    windows = [_in("Moxie?"), _in("tell me a joke")]
    _ask(rt, did, "tell me a joke", extra_lines=windows)
    _notify_raw(rt, did, speech="Why did the cow cross?", extra_lines=windows)
    assert _lines(rt, did)[-2:] == [("user", "Moxie? tell me a joke"),
                                    ("assistant", "Why did the cow cross?")]


def test_a_report_naming_two_turns_retires_every_turn_older_than_the_newer_one(tmp_path):
    """"hello" is answered and never reported; "P one" and "P two" are answered as turns
    of their own and reported together. The report names both, so every turn older than
    the newer one goes, "hello" and "P one" alike (not only the turns older than the
    oldest it names), and neither can take a later line."""
    rt, did = _runtime(tmp_path, _Brain(answers=WINDOWS))
    _ask(rt, did, "hello")
    _ask(rt, did, "P one")
    _ask(rt, did, "P two")
    _notify_raw(rt, did, speech="A one. A two.", extra_lines=[_in("P one"), _in("P two")])
    assert [" ".join(r.child) for r in rt._turn_records()[did]] == ["p two"]
    assert _lines(rt, did) == [("user", "hello"), ("assistant", "Okay!")] + BOTH


def test_an_unanswered_window_joins_the_turn_it_was_reported_with_never_an_older_one(
        tmp_path):
    """"no", answered "Okay! Let's play a game." and cut at "Okay!"; "yes"; then "wait" and
    "no" in one prompt, reported together. "wait" joins the third turn's "no", the line it
    was reported with. (Before: the cut-off first turn still matched "no", so "wait" was
    joined two turns back, before that turn's "Okay!", the third turn's child line stayed
    a bare "no", and the first turn's cut was undone by the third turn's answer.)"""
    rt, did = _runtime(tmp_path, _Brain(answers={"no": " ".join(GAME), "yes": "Sure!"}))
    _ask(rt, did, "no")
    _notify(rt, did, GAME[0], said="no")
    _ask(rt, did, "yes")
    _notify(rt, did, "Sure!", said="yes")
    windows = [_in("wait"), _in("no")]
    _ask(rt, did, "no", extra_lines=windows)
    _notify_raw(rt, did, speech=" ".join(GAME), extra_lines=windows)
    assert _lines(rt, did) == [("user", "no"), ("assistant", GAME[0]),
                               ("user", "yes"), ("assistant", "Sure!"),
                               ("user", "wait no"), ("assistant", " ".join(GAME))]


def test_the_child_asking_a_cut_off_question_again_later_keeps_the_cut(tmp_path):
    """The child asks about the moon, cuts Moxie off at "Rock and", says "cool", asks again
    and hears the whole answer. The first turn stays what Moxie got through and the whole
    answer is the third turn's. (Before: the first turn, still matchable by its child
    line, took the report, and its entry became the whole answer again.)"""
    rt, did = _runtime(tmp_path, _Brain())
    _ask(rt, did, PROMPT)
    _notify(rt, did, "Rock and", said=PROMPT)
    _ask(rt, did, "cool")
    _notify(rt, did, "Okay!", said="cool")
    _ask(rt, did, PROMPT)
    _notify(rt, did, ANSWER, said=PROMPT)
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", "Rock and"),
                               ("user", "cool"), ("assistant", "Okay!"),
                               ("user", PROMPT), ("assistant", ANSWER)]


@pytest.mark.parametrize("earlier", [None, "wait"], ids=["one-window", "an-earlier-window"])
def test_the_child_repeating_a_cut_off_line_at_once_names_the_new_turn_by_the_words_unsaid(
        tmp_path, earlier):
    """"no", cut at "Okay!" of "Okay! Let's play a game."; "no" again straight away, the
    same scripted answer spoken whole and reported with the same child line. No later turn
    has been reported, so the cut-off turn is still there and the child's line names both
    turns: the report is the one whose text holds its words all unreported, the new turn
    (on the cut-off turn "Okay!" was reported already: there it would be a restart). The
    cut stays, and an earlier window the child said with the repeat ("wait") joins the
    new turn's line, not the cut-off turn's. (Before: the oldest turn not reported in full
    took the report, undoing the cut, and the window with it.)"""
    rt, did = _runtime(tmp_path, _Brain(answers={"no": GAME}))
    _ask(rt, did, "no")
    _notify(rt, did, GAME[0], said="no")
    lines = [_in(earlier), _in("no")] if earlier else [_in("no")]
    _ask(rt, did, "no", extra_lines=lines)
    _notify_raw(rt, did, speech=" ".join(GAME), extra_lines=lines)
    child = f"{earlier} no" if earlier else "no"
    assert _lines(rt, did) == [("user", "no"), ("assistant", GAME[0]),
                               ("user", child), ("assistant", " ".join(GAME))]


def test_a_per_chunk_report_carrying_the_childs_line_again_continues_the_same_turn(tmp_path):
    """The same two turns, the first spoken whole, from a robot that repeats the child's
    line on every chunk's report, every report late. Each report fits both turns alike, so
    it names the oldest not yet reported in full: the second chunk continues the first
    turn; the third report ("Okay!" again) is a restart and opens the second."""
    rt, did = _runtime(tmp_path, _Brain(answers={"no": GAME}))
    _ask(rt, did, "no")
    _ask(rt, did, "no")
    for spoken in (GAME[0], GAME[1], GAME[0], GAME[1]):
        _notify(rt, did, spoken, said="no")
    assert _lines(rt, did) == [("user", "no"), ("assistant", " ".join(GAME))] * 2


def test_late_reports_of_two_turns_with_the_same_line_name_them_in_arrival_order(tmp_path):
    """Both "no" turns answered before a report lands (reports held while the second turn
    was open replay the same way): the first was cut at "Okay!". The first report names
    the oldest turn not reported in full; the second, "Okay!" again, is a restart there
    and names the next turn; the chunk after it continues that one. The cut stays on the
    first turn, the whole answer on the second."""
    rt, did = _runtime(tmp_path, _Brain(answers={"no": GAME}))
    _ask(rt, did, "no")
    _ask(rt, did, "no")
    _notify(rt, did, GAME[0], said="no")                  # the first turn, cut
    _notify(rt, did, GAME[0], said="no")                  # the second turn's first chunk
    _notify(rt, did, GAME[1])
    assert _lines(rt, did) == [("user", "no"), ("assistant", GAME[0]),
                               ("user", "no"), ("assistant", " ".join(GAME))]


def test_a_report_holding_a_restarted_answer_and_more_words_names_the_new_turn(tmp_path):
    """The same two turns, the first cut at "Okay!"; the second answer is reported whole
    with a line of the robot's own spoken in the same breath. The whole text is found in
    both turns and is marked on the one least reported, the new turn (on the cut-off turn
    it would be a restart); the robot's own line follows it. The cut stays. (Before: the
    oldest turn holding the text took it, undoing the cut.)"""
    rt, did = _runtime(tmp_path, _Brain(answers={"no": GAME}))
    _ask(rt, did, "no")
    _notify(rt, did, GAME[0], said="no")
    _ask(rt, did, "no")
    _notify(rt, did, f"{' '.join(GAME)} Ready?", said="no")
    assert _lines(rt, did) == [("user", "no"), ("assistant", GAME[0]),
                               ("user", "no"), ("assistant", " ".join(GAME)),
                               ("assistant", "Ready?")]


def test_late_per_chunk_reports_of_two_turns_ending_alike_re_assemble_each_in_order(tmp_path):
    """Both turns answered whole before their reports land, each ending with the same
    question, reported a chunk at a time. A chunk with no child line is the oldest turn's
    that holds its words unreported (the robot speaks in order): the first question is the
    moon turn's, the second the stars turn's, and neither turn is cut."""
    rt, did = _runtime(tmp_path, _Brain(answers=ENDINGS))
    _ask(rt, did, "tell me about the moon")
    _ask(rt, did, "and the stars?")
    _notify(rt, did, MOON[0], said="tell me about the moon")
    _notify(rt, did, MOON[1])
    _notify(rt, did, STARS[0], said="and the stars?")
    _notify(rt, did, STARS[1])
    assert _lines(rt, did) == [("user", "tell me about the moon"),
                               ("assistant", " ".join(MOON)),
                               ("user", "and the stars?"), ("assistant", " ".join(STARS))]


# --------------------------------------------------------------------------- #
# The report's child line says whose words they are
# --------------------------------------------------------------------------- #
#: A report that carries the child's line is the robot's own tie between that line and
#: the words it spoke for it. When those words also fit an older turn the robot has not
#: reported (one it skipped, or the unsaid tail of one the child cut off), they are the
#: named turn's. The virtual robot's per-chunk cadence (`--notify chunk`) carries the
#: child's line on the first chunk only, which is the shape below.
def test_a_report_naming_a_turn_marks_that_turns_words_not_an_older_unreported_turns(
        tmp_path):
    """"no" is answered "Okay! Let's play a game." and never reported (the robot skipped
    it); "yes" is answered "Okay! Sure!" in two chunks, the first reported with the
    child's line. "Okay!" is a run in both texts; the child's line names the second turn,
    so the words are its. (Before: the oldest turn holding the words took them, so the
    never-spoken first turn was cut to "Okay!" and retired, and the brain read a cut
    Moxie never made.)"""
    rt, did = _runtime(tmp_path, _Brain(answers={"no": " ".join(GAME),
                                                 "yes": ("Okay!", "Sure!")}))
    _ask(rt, did, "no")
    _ask(rt, did, "yes")
    _notify(rt, did, "Okay!", said="yes")
    assert _lines(rt, did) == [("user", "no"), ("assistant", " ".join(GAME)),
                               ("user", "yes"), ("assistant", "Okay!")]
    _notify(rt, did, "Sure!")
    assert _lines(rt, did) == [("user", "no"), ("assistant", " ".join(GAME)),
                               ("user", "yes"), ("assistant", "Okay! Sure!")]


def test_a_cut_off_turn_keeps_its_cut_when_the_next_turns_first_chunk_fits_its_unsaid_tail(
        tmp_path):
    """The moon answer "Yes! What do you think?" is cut at "Yes!"; the stars answer opens
    with that same question, reported with the child's line. The question is the stars
    turn's, named by the line, and the moon turn keeps its cut. (Before: the question fit
    the moon turn's unsaid tail first, the cut was undone and the moon turn retired holding
    words Moxie never said; the stars turn was marked nothing.)"""
    rt, did = _runtime(tmp_path, _Brain(answers={
        "tell me about the moon": "Yes! What do you think?",
        "and the stars?": ("What do you think?", "I think stars.")}))
    _ask(rt, did, "tell me about the moon")
    _notify(rt, did, "Yes!", said="tell me about the moon")        # the child cut in
    _ask(rt, did, "and the stars?")
    _notify(rt, did, "What do you think?", said="and the stars?")
    _notify(rt, did, "I think stars.")
    assert _lines(rt, did) == [("user", "tell me about the moon"), ("assistant", "Yes!"),
                               ("user", "and the stars?"),
                               ("assistant", "What do you think? I think stars.")]


def test_a_robot_that_carries_the_childs_line_on_every_chunk_keeps_the_cut_the_same_way(
        tmp_path):
    """The same two turns from a robot that repeats the child's line on every chunk's
    report (the other cadence a real Moxie may use; which one is not captured): each
    report names the stars turn, so the question and the sentence after it are its, and
    the moon turn keeps its cut. (Red on the round-2 head, whose run pass tried the turns
    oldest first whatever the child's line named.)"""
    rt, did = _runtime(tmp_path, _Brain(answers={
        "tell me about the moon": ("Yes!", "What do you think?"),
        "and the stars?": ("What do you think?", "I think stars.")}))
    _ask(rt, did, "tell me about the moon")
    _notify(rt, did, "Yes!", said="tell me about the moon")        # the child cut in
    _ask(rt, did, "and the stars?")
    _notify(rt, did, "What do you think?", said="and the stars?")
    _notify(rt, did, "I think stars.", said="and the stars?")
    assert _lines(rt, did) == [("user", "tell me about the moon"), ("assistant", "Yes!"),
                               ("user", "and the stars?"),
                               ("assistant", "What do you think? I think stars.")]


def test_a_cut_reported_after_the_childs_next_prompt_still_keeps_its_cut(tmp_path):
    """The child cuts the moon answer at "Yes!" and asks about the stars before the robot
    reports the cut: that report lands while the stars turn is open and is held, with the
    stars turn's first chunk behind it, until the turn is remembered; both then replay in
    order and each names its own turn. The moon turn keeps "Yes!", and the stars answer,
    which opens with the moon answer's unsaid question, is whole. (Red on the round-2
    head: the question fit the moon turn's unsaid tail first and undid the cut.)"""
    app = _GatedStream(answers={"tell me about the moon": "Yes! What do you think?"},
                       stream=("and the stars?", ("What do you think?", "I think stars.")))
    rt, did = _runtime(tmp_path, app)
    _ask(rt, did, "tell me about the moon")
    _push(rt, did, "and the stars?")                                # open after chunk 0
    _published(rt, did, 1)
    _notify(rt, did, "Yes!", said="tell me about the moon")        # the cut, reported late
    _notify(rt, did, "What do you think?", said="and the stars?")  # held behind it
    app.gate.set()
    rt._pool.shutdown(wait=True)
    _notify(rt, did, "I think stars.")
    assert _lines(rt, did) == [("user", "tell me about the moon"), ("assistant", "Yes!"),
                               ("user", "and the stars?"),
                               ("assistant", "What do you think? I think stars.")]


def test_a_report_naming_a_turn_takes_its_whole_text_over_an_older_turn_holding_it(
        tmp_path):
    """"no" is answered "Okay! Sure! Let's go." and never reported; "yes" is answered
    "Okay! Sure!" and reported whole with the child's line. The text is a run in the older
    turn too; the named turn takes it. (Before: the older turn took it and was cut to
    "Okay! Sure!", dropping "Let's go." from a turn the robot never reported on.)"""
    rt, did = _runtime(tmp_path, _Brain(answers={"no": "Okay! Sure! Let's go.",
                                                 "yes": "Okay! Sure!"}))
    _ask(rt, did, "no")
    _ask(rt, did, "yes")
    _notify(rt, did, "Okay! Sure!", said="yes")
    assert _lines(rt, did) == [("user", "no"), ("assistant", "Okay! Sure! Let's go."),
                               ("user", "yes"), ("assistant", "Okay! Sure!")]


def test_a_report_with_words_around_a_named_turns_text_marks_that_turn_not_an_older_one(
        tmp_path):
    """"no" is answered "Okay!" and never reported; "yes" is answered "Okay! Sure!" and
    reported with a line of the robot's own in the same breath and the child's line. Both
    turns' whole texts are inside the report; the named turn is marked and only "Ready?"
    is the robot's own. (Before: the older turn was marked first, "Sure! Ready?" became a
    second Moxie line, and "Sure!" was in history twice.)"""
    rt, did = _runtime(tmp_path, _Brain(answers={"no": "Okay!", "yes": "Okay! Sure!"}))
    _ask(rt, did, "no")
    _ask(rt, did, "yes")
    _notify(rt, did, "Okay! Sure! Ready?", said="yes")
    assert _lines(rt, did) == [("user", "no"), ("assistant", "Okay!"),
                               ("user", "yes"), ("assistant", "Okay! Sure!"),
                               ("assistant", "Ready?")]


def test_a_repeat_of_an_older_turns_words_reported_for_another_turn_is_a_line_of_its_own(
        tmp_path):
    """"no" is answered "Okay!" and reported; "yes" is answered "Sure!" and the robot
    reports "Okay!" for it. The report names the "yes" turn, so the "no" turn, which the
    robot has spoken past, takes no repeat of its words: the line is kept as the robot's
    own, after the "yes" turn's answer. (Red on the round-3 head: taken as a repeat report
    of the "no" turn, so the line was dropped.)"""
    rt, did = _runtime(tmp_path, _Brain(answers={"yes": "Sure!"}))
    _ask(rt, did, "no")
    _notify(rt, did, "Okay!", said="no")
    _ask(rt, did, "yes")
    _notify(rt, did, "Okay!", said="yes")
    assert _lines(rt, did) == [("user", "no"), ("assistant", "Okay!"),
                               ("user", "yes"), ("assistant", "Sure!"),
                               ("assistant", "Okay!")]


def test_a_cut_off_turn_takes_no_restart_of_its_words_reported_for_another_turn(tmp_path):
    """"no" is answered "Okay! Let's play a game." and cut at "Okay!"; "yes" is answered
    "Sure!" and the robot reports the game line whole for it. Those words start over
    inside words the cut-off turn has reported, and the report names the "yes" turn: the
    cut stays and the line is the robot's own, after the "yes" turn's answer. (Red on the
    round-3 head: the cut-off turn took the run as a partly reported one, undoing the
    cut.)"""
    rt, did = _runtime(tmp_path, _Brain(answers={"no": " ".join(GAME), "yes": "Sure!"}))
    _ask(rt, did, "no")
    _notify(rt, did, GAME[0], said="no")                           # the child cut in
    _ask(rt, did, "yes")
    _notify(rt, did, " ".join(GAME), said="yes")
    assert _lines(rt, did) == [("user", "no"), ("assistant", GAME[0]),
                               ("user", "yes"), ("assistant", "Sure!"),
                               ("assistant", " ".join(GAME))]


def test_a_cut_off_answer_said_again_whole_with_no_child_line_names_the_new_turn(tmp_path):
    """"no" is answered "Okay! Let's play a game." and cut at "Okay!"; "yes" gets the same
    scripted answer, which the robot reports whole with no child line. With no line to
    name a turn, the words go where they fit best: on the cut-off turn they would start
    over inside words already reported, on the new turn none are, so the new turn takes
    them and the cut stays. (A first-run-wins rule would undo the cut.)"""
    rt, did = _runtime(tmp_path, _Brain(answers={"no": " ".join(GAME),
                                                 "yes": " ".join(GAME)}))
    _ask(rt, did, "no")
    _notify(rt, did, GAME[0], said="no")                           # the child cut in
    _ask(rt, did, "yes")
    _notify(rt, did, " ".join(GAME))
    assert _lines(rt, did) == [("user", "no"), ("assistant", GAME[0]),
                               ("user", "yes"), ("assistant", " ".join(GAME))]


# --------------------------------------------------------------------------- #
# A turn superseded while its stream was open
# --------------------------------------------------------------------------- #
#: The child speaks again before a streamed answer closes (`MOXIE_STREAMING` is on by
#: default: a long answer, a barge-in after the first sentence): the stream is cancelled
#: and the turn is never remembered. The robot spoke the chunk it got and reports it with
#: the child's line, while the turn is open (held) or after the next prompt. The chunks
#: the runtime sent are that turn's record (`_spoke`), and its lines are written as the
#: robot reports them, never on an older turn. (Red on the round-3 head, which voided the
#: record and matched its reports against the older turns: the child's repeated "no" and
#: Moxie's chunk were dropped or moved there, and an older cut was undone.)


class _Scripted(MoxieApp):
    """Answers turns in order from `script`, `[(child line, chunks, gated), …]`: one
    chunk is a plain answer (held until `release` when gated), several a stream, one chunk
    per sentence, waiting on `gate` before the second when gated."""
    name = "scripted"

    def __init__(self, script, gate_at=1):
        self.script = list(script)
        self.gate = threading.Event()
        self.gate_at = gate_at                    # the chunk a gated stream waits before
        self.release = threading.Event()
        self.seen = []

    def respond_stream(self, turn):
        speech, chunks, gated = self.script[0]
        if len(chunks) < 2:
            return None
        self.script.pop(0)
        assert speech == turn.speech, (speech, turn.speech)
        self.seen.append(list(turn.history))
        gate, gate_at = self.gate, self.gate_at

        def chunks_of():
            for i, text in enumerate(chunks):
                if i == gate_at and gated:
                    assert gate.wait(PATIENCE), "the test never opened the gate"
                yield ReplyChunk(text=text, final=i == len(chunks) - 1)
        return chunks_of()

    def respond(self, turn):
        speech, chunks, gated = self.script.pop(0)
        assert speech == turn.speech, (speech, turn.speech)
        if gated:
            assert self.release.wait(PATIENCE), "the test never released the answer"
        self.seen.append(list(turn.history))
        return Reply(text=" ".join(chunks))


def _barge_in(rt, did, app, before, reported, said, then):
    """The open streamed turn spoke its first chunk (`before` replies were published
    ahead of it); the robot reports it with the child's line, held; the child barges in
    with `then`, answered plainly, and the stream is let run into its cancel."""
    _published(rt, did, before)
    _notify(rt, did, reported, said=said)
    _push(rt, did, then)
    app.gate.set()
    rt._pool.shutdown(wait=True)
    fresh_pool(rt)


def test_a_superseded_turns_report_keeps_the_childs_repeated_line(tmp_path):
    """"no" is answered "Okay!" and reported; the child says "no" again, the answer
    streams "Sure thing." and the child barges in with "stop". The second "no" and "Sure
    thing." are the superseded turn's lines, written as reported, and the brain reads
    them so at the next prompt. (Red on the round-3 head: the second "no" was dropped as a
    line the first turn held, and "Sure thing." appended bare.)"""
    app = _Scripted([("no", ["Okay!"], False), ("no", ["Sure thing.", "Let's go."], True),
                     ("stop", ["Fine."], False), ("bye", ["Bye!"], False)])
    rt, did = _runtime(tmp_path, app)
    _ask(rt, did, "no")
    _notify(rt, did, "Okay!", said="no")
    _push(rt, did, "no")
    _barge_in(rt, did, app, 1, "Sure thing.", "no", "stop")
    want = [("user", "no"), ("assistant", "Okay!"), ("user", "no"), ("assistant", "Sure thing."),
            ("user", "stop"), ("assistant", "Fine.")]
    assert _lines(rt, did) == want
    _notify(rt, did, "Fine.", said="stop")
    _ask(rt, did, "bye")
    assert [(m["role"], m["content"]) for m in app.seen[-1]] == want


def test_a_superseded_turn_answered_as_the_previous_one_was_keeps_both_exchanges(tmp_path):
    """The same, the second answer opening with the first one's words. (Red on the round-3
    head: Moxie's second "Okay!" was a repeat report of the first turn, and the second
    "no" went with it: the whole exchange vanished.)"""
    app = _Scripted([("no", ["Okay!"], False), ("no", ["Okay!", "Let's play a game."], True),
                     ("stop", ["Fine."], False)])
    rt, did = _runtime(tmp_path, app)
    _ask(rt, did, "no")
    _notify(rt, did, "Okay!", said="no")
    _push(rt, did, "no")
    _barge_in(rt, did, app, 1, "Okay!", "no", "stop")
    assert _lines(rt, did) == [("user", "no"), ("assistant", "Okay!"),
                               ("user", "no"), ("assistant", "Okay!"),
                               ("user", "stop"), ("assistant", "Fine.")]


def test_a_superseded_turns_report_never_undoes_an_older_cut(tmp_path):
    """The moon answer is cut at "Yes!"; the stars answer streams the moon answer's unsaid
    question first and the child barges in. The question is the stars turn's, named by the
    child's line, and the moon turn keeps its cut. (Red on the round-3 head: the question
    landed on the moon turn's unsaid tail, undoing the cut, and the stars turn had no
    Moxie line.)"""
    app = _Scripted([("tell me about the moon", ["Yes! What do you think?"], False),
                     ("and the stars?", ["What do you think?", "I think stars."], True),
                     ("wait", ["Okay."], False)])
    rt, did = _runtime(tmp_path, app)
    _ask(rt, did, "tell me about the moon")
    _notify(rt, did, "Yes!", said="tell me about the moon")        # the child cut in
    _push(rt, did, "and the stars?")
    _barge_in(rt, did, app, 1, "What do you think?", "and the stars?", "wait")
    assert _lines(rt, did) == [("user", "tell me about the moon"), ("assistant", "Yes!"),
                               ("user", "and the stars?"), ("assistant", "What do you think?"),
                               ("user", "wait"), ("assistant", "Okay.")]


def test_a_superseded_turns_opener_is_not_a_repeat_of_the_previous_turns(tmp_path):
    """"no" is answered "Okay! Let's play." and reported whole; "yes" streams "Okay!" and
    the child barges in. "Okay!" is the "yes" turn's, not a repeat report of the "no"
    turn's. (Red on the round-3 head: swallowed as a repeat, so "yes" stood alone.)"""
    app = _Scripted([("no", ["Okay! Let's play."], False), ("yes", ["Okay!", "Here we go."], True),
                     ("stop", ["Fine."], False)])
    rt, did = _runtime(tmp_path, app)
    _ask(rt, did, "no")
    _notify(rt, did, "Okay! Let's play.", said="no")
    _push(rt, did, "yes")
    _barge_in(rt, did, app, 1, "Okay!", "yes", "stop")
    assert _lines(rt, did) == [("user", "no"), ("assistant", "Okay! Let's play."),
                               ("user", "yes"), ("assistant", "Okay!"),
                               ("user", "stop"), ("assistant", "Fine.")]


def test_a_superseded_turn_is_named_before_an_older_turn_holding_the_same_words_unsaid(
        tmp_path):
    """"no" is answered "Okay! Let's play." and never reported (the robot skipped it);
    "no" again streams "Okay!" and the child barges in. The report names both turns and
    both hold "Okay!" unreported; the superseded turn is the newer one and the robot's
    report is all it will ever have in history, so it takes the report, and the skipped
    answer is left whole. (Red on the round-3 head: the older turn was cut to "Okay!" and
    the second "no" dropped.)"""
    app = _Scripted([("no", ["Okay! Let's play."], False), ("no", ["Okay!", "Here we go."], True),
                     ("stop", ["Fine."], False)])
    rt, did = _runtime(tmp_path, app)
    _ask(rt, did, "no")
    _push(rt, did, "no")
    _barge_in(rt, did, app, 1, "Okay!", "no", "stop")
    assert _lines(rt, did) == [("user", "no"), ("assistant", "Okay! Let's play."),
                               ("user", "no"), ("assistant", "Okay!"),
                               ("user", "stop"), ("assistant", "Fine.")]


def test_a_superseded_turns_chunks_reported_one_by_one_with_the_line_on_each_are_one_turn(
        tmp_path):
    """Two sentences of the answer are spoken and reported, each with the child's line, as
    a robot may do on every chunk, before the child barges in ahead of the third. The
    reports write the child's line once and Moxie's words as the robot got through them,
    one entry re-assembled chunk by chunk. (Red on the round-3 head: the line dropped as
    the first turn's, "Okay!" swallowed as its repeat, "Let's play." appended bare.)"""
    app = _Scripted([("no", ["Okay!"], False), ("no", ["Okay!", "Let's play.", "Ready?"], True),
                     ("stop", ["Fine."], False)], gate_at=2)
    rt, did = _runtime(tmp_path, app)
    _ask(rt, did, "no")
    _notify(rt, did, "Okay!", said="no")
    _push(rt, did, "no")
    _published(rt, did, 1, how_many=2)
    _notify(rt, did, "Okay!", said="no")
    _notify(rt, did, "Let's play.", said="no")
    _push(rt, did, "stop")
    app.gate.set()
    rt._pool.shutdown(wait=True)
    assert _lines(rt, did) == [("user", "no"), ("assistant", "Okay!"),
                               ("user", "no"), ("assistant", "Okay! Let's play."),
                               ("user", "stop"), ("assistant", "Fine.")]


def test_a_superseded_turns_report_landing_after_the_next_prompt_is_still_its_own(tmp_path):
    """The child says "no, a joke" while the story's first chunk ("Okay!") is being
    spoken; the robot reports that chunk after the new prompt, so it is held by the joke
    turn, whose answer opens with the same word. Replayed as the joke turn is remembered,
    the report names the story turn by the child's line and its words are written as its
    own, before the joke turn's lines. (Red on the round-3 head: credited to the joke
    turn's text, so the story's "Okay!" vanished and "tell me a story" was appended
    bare.)"""
    app = _Scripted([("tell me a story", ["Okay!", "Once upon a time."], True),
                     ("no, a joke", ["Okay! Why did the cow cross the road?"], True)])
    rt, did = _runtime(tmp_path, app)
    _push(rt, did, "tell me a story")
    _published(rt, did, 0)
    _push(rt, did, "no, a joke")                           # superseded; the joke waits
    _notify(rt, did, "Okay!", said="tell me a story")     # the story's chunk, reported late
    app.release.set()
    app.gate.set()
    rt._pool.shutdown(wait=True)
    _notify(rt, did, "Okay! Why did the cow cross the road?", said="no, a joke")
    assert _lines(rt, did) == [("user", "tell me a story"), ("assistant", "Okay!"),
                               ("user", "no, a joke"),
                               ("assistant", "Okay! Why did the cow cross the road?")]


def test_a_superseded_turn_that_sent_nothing_never_takes_the_next_turns_report(tmp_path):
    """The child says "no" twice before the brain answers: the first turn is superseded
    before any chunk went out, the second is answered "Okay!" and reported with the
    child's line. The first turn has no words the robot could have said, so it keeps no
    record and the report is the second turn's: the exchange is held once. (A superseded
    turn nameable by its line alone would take that report and write Moxie's line
    twice.)"""
    app = _GatedStream(release=threading.Event())
    rt, did = _runtime(tmp_path, app)
    rt.brain_budget_s = 0
    _push(rt, did, "no")
    _push(rt, did, "no")
    app.release.set()
    rt._pool.shutdown(wait=True)
    fresh_pool(rt)
    assert _lines(rt, did) == [("user", "no"), ("assistant", "Okay!")]
    assert len(rt._turn_records()[did]) == 1, "the answered turn's record, and no other"
    _notify(rt, did, "Okay!", said="no")
    assert _lines(rt, did) == [("user", "no"), ("assistant", "Okay!")]


def test_a_superseded_worker_past_its_stale_check_replaces_its_own_void_record(tmp_path):
    """The stream of "first" sent its sentence; the child's "second" lands, which keeps
    "first" as a void record; the old worker, past its stale check, remembers "first"
    whole all the same. Nothing of the void record was written, so the remembered record
    replaces it, and the robot's report of the sentence names one turn: the exchange is
    held once."""
    rt, did = _runtime(tmp_path, _Brain())
    rt._start_turn_record(did, "first")
    rt._spoke(did, "first", "One.")
    rt._start_turn_record(did, "second")                  # "first" void, holding "One."
    rt._remember(did, "first", "One.")                    # the old worker, past its check
    rt._remember(did, "second", "Two.")
    _notify(rt, did, "One.", said="first")
    _notify(rt, did, "Two.", said="second")
    assert _lines(rt, did) == [("user", "first"), ("assistant", "One."),
                               ("user", "second"), ("assistant", "Two.")]


def test_a_superseded_turns_line_reported_after_its_words_goes_before_them(tmp_path):
    """A robot that reports the superseded turn's words first and the child's line in a
    later notify (a shape no captured robot sends): the line is written before Moxie's
    words, as a turn's lines always are."""
    rt, did = _runtime(tmp_path, _Brain())
    rt._start_turn_record(did, "no")
    rt._spoke(did, "no", "Okay!")
    rt._start_turn_record(did, "stop")
    rt._remember(did, "stop", "Fine.")
    _notify(rt, did, "Okay!")
    _notify(rt, did, said="no")
    assert _lines(rt, did) == [("user", "stop"), ("assistant", "Fine."),
                               ("user", "no"), ("assistant", "Okay!")]


# --------------------------------------------------------------------------- #
# A bad report fails alone
# --------------------------------------------------------------------------- #
#: Shapes a protobuf-serialised robot cannot send (`RemoteChat.proto`: `speech` is a
#: string, `extra_lines` repeated contexts whose `text` is a string), as `_on_remote_chat`
#: hands them on.
MALFORMED = {
    "speech-not-a-string": {"speech": 5},
    "extra_lines-not-a-list": {"extra_lines": "oops"},
    "text-not-a-string": {"extra_lines": [{"context_type": "input", "text": 7}],
                          "speech": ANSWER},
}
BAD = pytest.mark.parametrize("bad", list(MALFORMED.values()), ids=list(MALFORMED))


@BAD
def test_a_malformed_notify_with_no_turn_open_is_dropped_with_one_line(tmp_path, bad, capsys):
    """origin/dev: `_ingest_notify` raised and `_on_message` dropped that one message with
    an "error handling" line. The same, one level down: dropped on arrival, nothing raised,
    history as it was, and the next good report still reconciles."""
    rt, did = _runtime(tmp_path, _Brain())
    _ask(rt, did, PROMPT)
    _notify_raw(rt, did, **bad)
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", ANSWER)]
    assert "dropped a malformed notify" in capsys.readouterr().out
    _notify(rt, did, ANSWER, said=PROMPT)
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", ANSWER)]


@BAD
def test_a_malformed_notify_held_while_a_plain_turn_is_open_never_costs_the_turn(tmp_path, bad):
    """A bad report arriving while the brain is still thinking. The first version of this
    branch held it unchecked and `_remember` raised on it before the turn's lines were
    written: the worker guard spoke the stock line and the turn vanished from history
    (origin/dev had dropped only the message). The answer is published and the turn is
    remembered once, whatever the robot sent."""
    app = _SlowBrain()
    rt, did = _runtime(tmp_path, app)
    rt.brain_budget_s = 0                          # no filler: the answer is the only line
    _push(rt, did, PROMPT)                         # the record opens before the worker runs
    _notify_raw(rt, did, **bad)
    app.release.set()
    rt._pool.shutdown(wait=True)
    assert _said(rt, did) == [ANSWER]
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", ANSWER)]


@BAD
def test_a_malformed_notify_held_while_a_stream_is_open_never_costs_the_turn(tmp_path, bad):
    """The streamed path remembers after publishing, so the first version lost the turn from
    history (and K5's goodbye summary never ran). Remembered once."""
    app = _GatedStream()
    rt, did = _runtime(tmp_path, app)
    _push(rt, did, PROMPT)
    _published(rt, did, 0)
    _notify_raw(rt, did, **bad)
    app.gate.set()
    rt._pool.shutdown(wait=True)
    assert _said(rt, did) == [SENTENCES[0], SENTENCES[1]]
    assert _lines(rt, did) == [("user", PROMPT),
                               ("assistant", f"{SENTENCES[0]} {SENTENCES[1]}")]


@BAD
def test_a_malformed_notify_held_by_a_superseded_turn_never_costs_the_next_prompt(
        tmp_path, bad):
    """Held by a turn the child then moves on from: replayed as the next turn starts, on the
    MQTT thread, before the pool is asked to answer. The first version raised there, so the
    child's next prompt went unanswered."""
    app = _GatedStream()
    rt, did = _runtime(tmp_path, app)
    _push(rt, did, PROMPT)
    _published(rt, did, 0)
    _notify_raw(rt, did, **bad)
    _push(rt, did, "never mind")                   # raised here before the fix
    app.gate.set()
    rt._pool.shutdown(wait=True)
    assert "Okay!" in _said(rt, did)
    assert _lines(rt, did) == [("user", "never mind"), ("assistant", "Okay!")]


def test_a_report_the_reconcile_cannot_handle_fails_alone(tmp_path, monkeypatch, capsys):
    """Behind the arrival check: a held report that makes the reconcile itself raise is
    logged and dropped, and the turn's lines are still written, on the worker
    (`_remember`) and on the MQTT thread (`_start_turn_record`) alike."""
    app = _SlowBrain()
    rt, did = _runtime(tmp_path, app)
    rt.brain_budget_s = 0

    def boom(*_args, **_kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(rt, "_reconcile_notify", boom)
    _push(rt, did, PROMPT)
    _notify(rt, did, ANSWER, said=PROMPT)          # held, well formed
    app.release.set()
    rt._pool.shutdown(wait=True)
    fresh_pool(rt)
    first = [("user", PROMPT), ("assistant", ANSWER)]
    assert _said(rt, did) == [ANSWER]
    assert _lines(rt, did) == first
    app.release.clear()
    _push(rt, did, "second")                       # open, its answer held back
    _notify(rt, did, ANSWER, said="second")        # held
    _push(rt, did, "third")                        # replays the held report: boom, contained
    app.release.set()
    rt._pool.shutdown(wait=True)
    assert _lines(rt, did) == first + [("user", "third"), ("assistant", ANSWER)]
    assert capsys.readouterr().out.count("dropped a notify") == 2


# --------------------------------------------------------------------------- #
# A robot that never notifies: today's history, byte for byte
# --------------------------------------------------------------------------- #
#: The redirect the golden's safety stage says (the shipped rules pick one at random).
REDIRECT = "Let's talk about something else."


class _FixedRedirect:
    """The shipped safety rules with one redirect line, so a golden can hold it."""
    name = "rules"

    def __init__(self):
        from moxie_sdk import safety as safety_seam
        self._rules = safety_seam.default_classifier()

    def assess(self, text, role=None):
        return self._rules.assess(text, role=role)

    def redirect(self, verdict, last=""):
        from moxie_sdk.safety import Redirect
        return Redirect(text=REDIRECT, markup=REDIRECT, phrase_id=1)


#: One of each kind of turn: plain, streamed, a hello opening a streamed and a plain
#: turn, a line the safety gate blocks, and a line the child says twice.
GOLDEN_TURNS = ((PROMPT, None), ("tell me about the moon", None), ("hi again", HELLO),
                ("and then?", HELLO), ("how do I make a bomb", None), ("no", None),
                ("no", None))

#: `rt.history[device]` after `GOLDEN_TURNS` on a robot that never notifies, recorded
#: on origin/dev 843b3862 (before this change); the transcript file is `json.dumps` of
#: it. The streamed turn's hello opens its line; the plain turn's hello is not history;
#: the blocked line never is.
GOLDEN = [
    {"role": "user", "content": PROMPT},
    {"role": "assistant", "content": ANSWER},
    {"role": "user", "content": "tell me about the moon"},
    {"role": "assistant", "content": STREAMED},
    {"role": "user", "content": "hi again"},
    {"role": "assistant", "content": f"{HELLO} {STREAMED}"},
    {"role": "user", "content": "and then?"},
    {"role": "assistant", "content": "Okay!"},
    {"role": "assistant", "content": REDIRECT},
    {"role": "user", "content": "no"},
    {"role": "assistant", "content": "Okay!"},
    {"role": "user", "content": "no"},
    {"role": "assistant", "content": "Okay!"},
]


def _never_notifies(tmp_path, monkeypatch):
    memdir = tmp_path / "memory"
    monkeypatch.setenv("MOXIE_MEMORY_DIR", str(memdir))
    rt, did = _runtime(tmp_path, _Brain(streamed={"tell me about the moon", "hi again"}))
    rt.safety = _FixedRedirect()
    for speech, hello in GOLDEN_TURNS:
        if hello:
            rt._pending_opener[did] = hello
        _ask(rt, did, speech)
    return rt.history[did], _on_disk(memdir, did)


def test_a_robot_that_never_notifies_keeps_todays_history_byte_for_byte(tmp_path,
                                                                        monkeypatch):
    history, stored = _never_notifies(tmp_path, monkeypatch)
    assert history == GOLDEN
    assert stored == json.dumps(GOLDEN)


# --------------------------------------------------------------------------- #
# What the brain and the goodbye summary read
# --------------------------------------------------------------------------- #
MODULE = {"conversations": [{
    "name": "Memory Chat", "module_id": "MCHAT", "content_id": "default",
    "prompt": "Talking to {{ volley.config.child_pii.nickname }}.",
    "memory": {"namespace": "mchat", "summarize": True, "min_volleys": 2},
}]}
SUMMARY = json.dumps({"facts": [], "preferences": [], "open_threads": [],
                      "summary": "They talked."})


def _content_brain(calls):
    """The content brain's chat seam: answers each child line with its own line, says
    goodbye with `<exit>` (which ends the conversation and runs the summary), and records
    every message list it is given (the summary request included)."""
    def chat(messages):
        calls.append([dict(m) for m in messages])
        if "JSON object" in messages[0]["content"]:
            return SUMMARY
        said = messages[-1]["content"]
        if said == "bye now":
            return "Bye! See you soon.<exit>"
        return f"Moxie answer to {said}."
    return chat


def test_after_25_reported_exchanges_the_brain_and_the_goodbye_summary_read_each_line_once(
        tmp_path):
    from moxie_sdk.content import ContentApp, load_module
    from moxie_sdk.memory_store import MemoryStore
    calls = []
    app = ContentApp(load_module(MODULE), _content_brain(calls),
                     memory=MemoryStore(JsonStore(str(tmp_path / "memory"))))
    rt, did = _runtime(tmp_path, app, module_id="MCHAT")
    want = []
    for n in range(1, 26):
        child, moxie = f"child line {n}", f"Moxie answer to child line {n}."
        _ask(rt, did, child)
        _notify(rt, did, moxie, said=child)
        want += [{"role": "user", "content": child}, {"role": "assistant", "content": moxie}]
    assert rt.history[did] == want and len(want) == 50

    _ask(rt, did, "bye now")
    goodbye_turn = next(c for c in calls if c[-1]["content"] == "bye now")
    window = goodbye_turn[1:-1]                   # turn.history[-max_history:] (40)
    assert window == want[-40:]
    summary = next(c for c in calls if "JSON object" in c[0]["content"])
    transcript = summary[0]["content"].split("Transcript:\n", 1)[1].splitlines()
    said = want + [{"role": "user", "content": "bye now"},
                   {"role": "assistant", "content": "Bye! See you soon."}]
    assert transcript == [f"{'Moxie' if m['role'] == 'assistant' else 'Child'}: "
                          f"{m['content']}" for m in said[-40:]]
    assert len(set(transcript)) == len(transcript) == 40


# --------------------------------------------------------------------------- #
# The SIL robot's notify is the contract's
# --------------------------------------------------------------------------- #
def test_the_sil_robot_reports_each_answer_once_with_the_childs_line(tmp_path):
    """`--notify` (the default "event" cadence): one notify per answered prompt, its
    joined chunks in `speech` and the child's line as an `input` extra_line; a module
    query's answer is not reported."""
    pytest.importorskip("paho.mqtt.client", reason="the SIL robot needs paho")
    from helpers_runtime import loopback
    from virtual_moxie import VirtualMoxie
    rt, did = _runtime(tmp_path, _Brain(streamed={PROMPT}))
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id=did, verbose=False,
                      notify="event")
    loopback(rt, vm)
    vm.send_prompt(PROMPT)
    rt._pool.shutdown(wait=True)
    fresh_pool(rt)
    vm.send_module_query()
    assert vm.module_list, "the module query was not answered"
    assert len(vm.notified) == 1, vm.notified
    sent = vm.notified[0]
    assert {k: sent[k] for k in ("command", "backend", "speech", "extra_lines")} == {
        "command": "notify", "backend": "router", "speech": STREAMED,
        "extra_lines": [{"context_type": "input", "text": PROMPT}]}
    assert _lines(rt, did) == [("user", PROMPT), ("assistant", STREAMED)]


def test_the_smoke_harness_runs_the_robot_with_notify_on():
    """`sim/run_smoke.sh` (CI's SIL smoke) drives the robot with `--notify`, so the
    standing smoke is a shape a real Moxie produces."""
    with open(os.path.join(REPO, "sim", "run_smoke.sh")) as fh:
        smoke = fh.read()
    call = smoke[smoke.index("python3 sim/virtual_moxie.py --host 127.0.0.1 --port $PORT "
                             "--timeout $CHAT_TIMEOUT"):]
    assert "--notify" in call[:call.index("rc=$?")]
