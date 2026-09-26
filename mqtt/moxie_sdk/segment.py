"""
Sentence segmentation for a *streaming* brain — turn a trickle of model tokens into
whole sentences the robot can speak as soon as each one is finished.

Each finished sentence goes out as its own REPLY_PENDING chunk, so the child hears words
at first-token latency instead of whole-completion latency (a live gateway turn can
exceed the robot's ~20 s reprompt window).

Pure and dependency-free: `feed` returns sentences that are definitely complete, `flush`
returns the tail. A boundary is `.`/`!`/`?` (+ closing quotes/brackets), whitespace, then
more text — so the LAST sentence always comes out of `flush()` and can close the turn.

Not split: decimals ("3.5"), abbreviations (`Dr.`, `e.g.`, initials), ellipses, and
sentences shorter than `min_chars` (they wait for the next one; `flush` emits a short
whole answer anyway).
"""
from __future__ import annotations

from typing import List

#: Sentence-final punctuation we split on.
TERMINALS = ".!?"

#: Closers that may sit between the terminal and the space: He said "stop!" Then…
_CLOSERS = "\"')]}”’»"

#: Tokens that end in a dot without ending a sentence (lower-cased, dot stripped).
#: Small on purpose — a miss merely splits a sentence early.
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
            # Require real text after the gap (keeps the final sentence for flush()).
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
