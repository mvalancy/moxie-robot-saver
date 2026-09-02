"""
The sentence segmenter (`moxie_sdk/segment.py`) — pure, no transport, no model.

This is the piece that turns a streaming brain into *speakable* chunks: a finished
sentence goes on the wire as its own `RemoteChatResponse` the moment the model writes
it, so a child hears real words at first-token latency instead of waiting 18-45 s for a
whole completion (docs/architecture/implementation-plan.md:138).

Everything a segmenter can get wrong costs the child something concrete, so each case
below is a thing we would otherwise ship: a split decimal ("three point... five"), a
split abbreviation ("Doctor... Seuss"), a split ellipsis, a lone "Hi." followed by a
gap, or — the subtle one — the LAST sentence escaping through `feed` so nothing is left
for `flush` to close the turn with.
"""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.segment import SentenceSegmenter, segment       # noqa: E402


def _stream(text, chunk=3, min_chars=None):
    """Feed `text` in small slices, exactly as a token stream would arrive."""
    seg = (SentenceSegmenter(min_chars=min_chars) if min_chars is not None
           else SentenceSegmenter())
    out = []
    for i in range(0, len(text), chunk):
        out += seg.feed(text[i:i + chunk])
    return out, seg


# --------------------------------------------------------------- boundaries
def test_splits_on_sentence_boundaries():
    text = ("The moon looks different every night. That is because sunlight hits it "
            "from a new angle! Isn't that neat?")
    assert segment(text) == [
        "The moon looks different every night.",
        "That is because sunlight hits it from a new angle!",
        "Isn't that neat?",
    ]


def test_the_same_split_arrives_token_by_token():
    """Chunking must not depend on where the network cut the stream."""
    text = ("The moon looks different every night. That is because sunlight hits it "
            "from a new angle! Isn't that neat?")
    for size in (1, 2, 5, 17):
        emitted, seg = _stream(text, chunk=size)
        assert emitted + seg.flush() == segment(text), size


def test_a_closing_quote_stays_with_its_sentence():
    text = 'She shouted "look at that!" Then the whole class laughed out loud.'
    assert segment(text) == ['She shouted "look at that!"',
                             "Then the whole class laughed out loud."]


# --------------------------------------------------------------- non-boundaries
def test_a_decimal_is_not_a_sentence_end():
    text = "A blue whale is about 30.5 metres long, which is longer than a bus!"
    assert segment(text) == [text]


def test_abbreviations_do_not_split():
    for text in ("We read a book by Dr. Seuss and it was very silly indeed.",
                 "Bedtime is at 8 p.m. so we have a little time left to play.",
                 "Bring a snack, a hat, etc. and we can go outside together."):
        assert segment(text) == [text], text


def test_initials_do_not_split():
    text = "My favourite writer is J. R. R. Tolkien and he wrote about hobbits."
    assert segment(text) == [text]


def test_an_ellipsis_mid_thought_does_not_split():
    text = "Hmm... let me think about that one for a second, okay?"
    assert segment(text) == [text]


def test_a_unicode_ellipsis_does_not_split():
    text = "Thinking, thinking… nearly there, I promise you that!"
    assert segment(text) == [text]


# --------------------------------------------------------------- minimum length
def test_a_tiny_sentence_waits_for_the_next_one():
    """A lone "Hi." then a pause reads as a broken robot, so short lines ride along."""
    assert segment("Hi. I missed you so much today, friend!") == [
        "Hi. I missed you so much today, friend!"]


def test_a_tiny_sentence_that_is_the_whole_answer_still_goes_out():
    assert segment("Hi.") == ["Hi."]


def test_min_chars_is_adjustable():
    assert segment("Hi. I missed you!", min_chars=1) == ["Hi.", "I missed you!"]


# --------------------------------------------------------------- flush contract
def test_the_last_sentence_always_comes_out_of_flush():
    """The property the streamer depends on: whatever the answer ends with is still in
    the buffer when the stream stops, so there is always a chunk left to close the
    sequence with (`consistency_control.is_completed`). A boundary is only confirmed by
    REAL text after it — trailing whitespace is not enough."""
    for text in ("One sentence and then another one right here. And the tail.",
                 "One sentence and then another one right here. And the tail. ",
                 "One sentence and then another one right here.",
                 "One sentence and then another one right here.   "):
        emitted, seg = _stream(text)
        assert seg.pending.strip(), f"nothing left to close the turn: {text!r}"
        tail = seg.flush()
        assert len(tail) == 1 and tail[0] == text.strip().split(". ")[-1].strip()
        assert emitted + tail == segment(text)


def test_flush_on_an_empty_stream_yields_nothing():
    seg = SentenceSegmenter()
    assert seg.feed("") == [] and seg.flush() == []


def test_whitespace_only_input_yields_nothing():
    assert segment("   \n  ") == []


def test_no_text_is_ever_lost():
    text = ("Wow! A blue whale weighs about 150 tonnes. That is more than 20 elephants, "
            "Mr. Sam. Isn't that wild?")
    joined = " ".join(segment(text))
    assert joined.replace("  ", " ") == text
