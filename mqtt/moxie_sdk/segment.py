"""
Sentence segmentation for a *streaming* brain — turn a trickle of model tokens into
whole sentences the robot can speak as soon as each one is finished.

Why this exists
---------------
A live turn through our LLM gateway was measured at **45 s healthy / 18 s degraded**
against the robot's **~20 s reprompt window** (docs/architecture/implementation-plan.md:138,
docs/architecture/openmoxie-feature-audit.md:347). PR #14 stopped the silence with one
filler line; it did not shorten the wait for real words. Streaming does: the first
sentence of an answer is finished after a handful of tokens, so the child hears actual
content at first-token latency instead of at whole-completion latency. Each finished
sentence goes out as its own `RemoteChatResponse` chunk (`result=REPLY_PENDING` +
`chunk_num`, closed by `consistency_control.is_completed` — see
docs/architecture/mqtt-and-conversation.md §4.5).

Design
------
Dependency-free and **pure**: no regex engine surprises, no NLTK, no model call. Feed it
whatever text arrives (`feed`), get back the sentences that are definitely complete;
`flush` at the end of the stream returns the tail.

A boundary is `.`/`!`/`?` — optionally followed by closing quotes/brackets — then
whitespace, then **more non-space text**. Requiring real text after the whitespace is
what makes the last sentence of an answer always come out of `flush()`, never out of
`feed()`: at the end of a stream there is nothing after the final period, so the tail
stays in the buffer and the caller can mark it as the closing chunk. (Without that rule a
completion ending in "…done. " would emit its last sentence from `feed` and leave `flush`
empty, and the streamer would have nothing left to close the sequence with.)

Four things deliberately do NOT split:
  * **decimals** — "3.5 hours" (the char after the dot is not whitespace anyway, but the
    rule is written down because it is the classic failure);
  * **abbreviations** — a small, cheap set (`Mr.`, `Dr.`, `e.g.`, `a.m.`, …) plus single
    capital initials (`J. R. R.`), matched on the token that ends at the dot;
  * **ellipses** — `...` and `…`, which an LLM writes mid-thought ("Hmmmm... okay!");
  * **very short chunks** — a sentence shorter than `min_chars` waits for the next one, so
    a child never hears a lone "Hi." followed by a pause. If it is the *whole* answer,
    `flush` emits it anyway.
"""
from __future__ import annotations

from typing import List

#: Sentence-final punctuation we split on.
TERMINALS = ".!?"

#: Closers that may sit between the terminal and the space: He said "stop!" Then…
_CLOSERS = "\"')]}”’»"

#: Tokens that end in a dot without ending a sentence. Lower-cased, dot stripped.
#: Small and cheap on purpose — a missed abbreviation merely splits a sentence early,
#: which is a smaller sin than a whole answer arriving as one chunk.
ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "st", "sr", "jr", "vs", "etc", "approx",
    "fig", "dept", "est", "min", "max", "no",
    "e.g", "i.e", "a.m", "p.m", "u.s", "u.k", "p.s",
})

#: Below this many characters a finished sentence waits for the next one instead of
#: going out alone. Roughly "Hi there, friend." — one short breath.
DEFAULT_MIN_CHARS = 24


def _token_before(buf: str, i: int) -> str:
    """The word ending at `buf[i]` (the terminal), lower-cased, without its final dot.

    `"...to Dr."` → `"dr"`; `"...at 9 a.m."` → `"a.m"`; `"...by J."` → `"j"`.
    """
    j = i
    while j > 0 and (buf[j - 1].isalnum() or buf[j - 1] == "."):
        j -= 1
    return buf[j:i].lower()


def _is_abbreviation(buf: str, i: int) -> bool:
    """True when the dot at `buf[i]` belongs to an abbreviation, not a sentence end."""
    tok = _token_before(buf, i)
    if not tok:
        return False
    if tok in ABBREVIATIONS:
        return True
    # A single letter is an initial ("J. R. R. Tolkien"), never a sentence.
    return len(tok) == 1 and tok.isalpha()


class SentenceSegmenter:
    """Incremental sentence splitter. `feed(text) -> [complete sentences]`.

    Stateful and single-threaded (one per turn). Everything it has not decided about
    stays in the buffer until `feed` sees enough context or `flush` gives up waiting.
    """

    def __init__(self, min_chars: int = DEFAULT_MIN_CHARS):
        self.min_chars = int(min_chars)
        self._buf = ""

    # -- inspection ---------------------------------------------------------
    @property
    def pending(self) -> str:
        """Whatever has not been emitted yet (for tests/diagnostics)."""
        return self._buf

    # -- the two operations -------------------------------------------------
    def feed(self, text: str) -> List[str]:
        """Add streamed text; return every sentence that is now definitely complete."""
        if not text:
            return []
        self._buf += text
        out: List[str] = []
        while True:
            cut = self._next_boundary()
            if cut is None:
                break
            sentence = self._buf[:cut].strip()
            self._buf = self._buf[cut:].lstrip()
            if sentence:
                out.append(sentence)
        return out

    def flush(self) -> List[str]:
        """End of stream: emit the tail (which is where the LAST sentence always is)."""
        tail = self._buf.strip()
        self._buf = ""
        return [tail] if tail else []

    # -- internals ----------------------------------------------------------
    def _next_boundary(self):
        """Index just past the first usable sentence end, or None if we need more text."""
        buf = self._buf
        i = 0
        while i < len(buf):
            ch = buf[i]
            if ch not in TERMINALS:
                i += 1
                continue
            if not self._terminates(buf, i):
                i += 1
                continue
            # Walk over any closing quotes/brackets glued to the punctuation.
            j = i + 1
            while j < len(buf) and buf[j] in _CLOSERS:
                j += 1
            if j >= len(buf):
                return None                      # need the next character to decide
            if not buf[j].isspace():
                i += 1                           # "3.5", "u.s.a" — not a boundary
                continue
            # Require real text after the gap. That is what keeps the FINAL sentence of
            # an answer in the buffer for flush() to close the turn with.
            if not buf[j:].strip():
                return None
            if len(buf[:j].strip()) < self.min_chars:
                i = j                            # too short to speak alone — keep going
                continue
            return j
        return None

    @staticmethod
    def _terminates(buf: str, i: int) -> bool:
        """Is `buf[i]` (a terminal char) really the end of a sentence?"""
        if buf[i] != ".":
            return True                          # ! and ? are never decimals/abbrevs
        # "..." — an ellipsis is a pause inside a thought, not an end. (The single
        # character "…" is not in TERMINALS at all, so it never reaches here.)
        if i > 0 and buf[i - 1] == ".":
            return False
        if i + 1 < len(buf) and buf[i + 1] == ".":
            return False
        # 3.5 — a digit on both sides of the dot.
        if 0 < i < len(buf) - 1 and buf[i - 1].isdigit() and buf[i + 1].isdigit():
            return False
        return not _is_abbreviation(buf, i)


def segment(text: str, min_chars: int = DEFAULT_MIN_CHARS) -> List[str]:
    """Split a whole (non-streamed) string the same way the streamer would."""
    seg = SentenceSegmenter(min_chars=min_chars)
    return seg.feed(text) + seg.flush()
