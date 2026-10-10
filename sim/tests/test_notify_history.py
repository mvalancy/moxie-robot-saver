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
    `answers` -> that answer; anything else -> "Okay!". Records the history each call saw."""
    name = "notify-brain"

    def __init__(self, streamed=(), answers=None):
        self.streamed = set(streamed)
        self.answers = dict(answers or {})
        self.seen = []

    def respond(self, turn):
        self.seen.append(list(turn.history))
        if turn.speech == PROMPT:
            return Reply(text=ANSWER)
        return Reply(text=self.answers.get(turn.speech, "Okay!"))

    def respond_stream(self, turn):
        if turn.speech not in self.streamed:
            return None
        self.seen.append(list(turn.history))
        return (ReplyChunk(text=s, final=i == len(SENTENCES) - 1)
                for i, s in enumerate(SENTENCES))


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
    """`PROMPT`: streams its first sentence, then waits for the test before the rest. Any
    other line: a plain answer (`answers`, else "Okay!"), held back until `release` when
    one is given."""
    name = "gated"

    def __init__(self, answers=None, release=None):
        self.gate = threading.Event()
        self.answers = dict(answers or {})
        self.release = release

    def respond(self, turn):
        if self.release is not None:
            assert self.release.wait(PATIENCE), "the test never released the plain answer"
        return Reply(text=self.answers.get(turn.speech, "Okay!"))

    def respond_stream(self, turn):
        if turn.speech != PROMPT:
            return None

        def chunks():
            yield ReplyChunk(text=SENTENCES[0])
            assert self.gate.wait(PATIENCE), "the test never opened the gate"
            yield ReplyChunk(text=SENTENCES[1], final=True)
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
    open belongs to the new turn, so a report of the new turn's first chunk, arriving
    before the new turn closes, is held for it rather than appended beside it."""
    rt, did = _runtime(tmp_path, _Brain())
    rt._start_turn_record(did, "first")
    rt._start_turn_record(did, "second")                  # the child moved on
    rt._remember(did, "first", "One.")                    # the old worker, past its check
    _notify(rt, did, "Two.", said="second")               # the new turn, still open
    assert _lines(rt, did) == [("user", "first"), ("assistant", "One.")]
    rt._remember(did, "second", "Two.")
    both = [("user", "first"), ("assistant", "One."), ("user", "second"), ("assistant", "Two.")]
    assert _lines(rt, did) == both
    _notify(rt, did, "One.", said="first")                # the old worker's turn, reported late
    assert _lines(rt, did) == both


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


def test_a_window_joins_the_turn_its_report_names_not_an_earlier_one_already_reported(
        tmp_path):
    """Two turns with the same child line, both reported late: the second report's extra
    window joins the second turn's line (the first is fully reported), in the order
    reported."""
    rt, did = _runtime(tmp_path, _Brain())
    _ask(rt, did, "no")
    _ask(rt, did, "no")
    _notify(rt, did, "Okay!", said="no")
    _notify_raw(rt, did, speech="Okay!", extra_lines=[_in("wait"), _in("no")])
    assert _lines(rt, did) == [("user", "no"), ("assistant", "Okay!"),
                               ("user", "wait no"), ("assistant", "Okay!")]


def test_a_turn_the_robot_has_reported_past_no_longer_absorbs_a_new_line(tmp_path):
    """Once the robot reports a later turn, an earlier one it accounted for is dropped from
    the records, so the child saying the same words again where no turn of ours answers
    (a module's own conversation) is a new line, not one already held."""
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
