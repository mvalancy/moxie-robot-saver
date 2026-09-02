"""
The markup floor — one spoken line in, behavior markup out. Pure, deterministic, stdlib.

Why
---
Moxie's voice is synthesized **on the robot**, from markup (docs/architecture/
mqtt-and-conversation.md §5.3). There is no TTS for a cloud to improve, so "better
speech" is literally "better markup": it is the only lever a server has on how alive the
robot feels. Before this module every app except `LLMApp` handed the runtime plain text
and the robot read it out like a speaker.

`annotate()` is the floor under every reply: a mood for the line, a `<usel>` delivery for
a question or an exclamation, an arm gesture on the words that carry the thought, a pause
at an internal sentence boundary, and a `Gesture_None` at the end so the body comes back
to rest. Everything it emits is checked against the frozen catalog in `vocab.py`, so a
line can never carry an asset id we have not actually recovered.

Prior art, credited
-------------------
The *behaviors* are ported from OpenMoxie's `site/hive/automarkup/` (MIT, (c) Justin
Beghtol) — gesture-per-sentence, word classes that carry a gesture, gesture spacing in
words, a probability so it is not mechanical, a `<break>` after an internal sentence end
but **never** after the final word (which would delay the robot's turn hand-back), and
`<usel genre>` from terminal punctuation. **No code and no data table was copied.** Their
engine is non-deterministic by design (`random.randint` spacing, an 80 % gesture roll)
and several of its gesture ids (`AUTO_GESTURE_ME`, `Gesture_We`, `Gesture_Small`,
`Gesture_Discard`) are theirs, not in our recovered catalog. Here the dice become a
blake2b digest of `(turn_key, chunk_index, sentence, word)` — same "not mechanical" feel,
byte-for-byte reproducible, so a golden test can pin it. Never Python's `hash()`: it is
salted per process and would differ across workers.

The rules, in one place
-----------------------
* **Mood** (one per line, `ePlaybackMood` 0-10). An explicit `mood_hint` wins; an unknown
  hint is dropped, never passed through. Otherwise the first matching cue class:
  apology -> 2 Sad, surprise -> 5 Surprised, mistake -> 4 Shy, thinking/question ->
  9 Curious, puzzlement -> 8 Confused, praise/celebration/`!` -> 1 Happy, else 0 Neutral.
  Intensity is `min(2, max(1, exclamations + emphatic words))` (`maxIntensity=2`).
* **Voice.** A sentence ending in `?` is wrapped in `<usel genre="question">`, one ending
  in `!` in `genre="excited"`. Neutral sentences are left alone (`genre="none"` is noise).
  `variant` is pinned to `"0"`: a variant is a recorded take and we have no evidence about
  which take suits which line.
* **Gesture.** One per clause, on the first word that carries the thought (self / you /
  question / high / low words, or a praise phrase), then a `Gesture_Talk` every 5 words —
  never inside the last two words of a sentence, where it would fight the closing rest
  pose. A sentence that plays a whole-body tree gets no arm gesture on top of it.
* **Tree.** At most one whole-body `Bht_*` per line, for three line types only: thinking
  -> `Bht_Active_Thinking`, greeting -> `Bht_Gesture_Greet`, sign-off -> `Bht_Sign_off`.
  A bare "Hi!" is deliberately *not* a greeting cue — the goldens pin that an interjection
  performs with mood and delivery, not with a whole-body wave.
* **Gaze.** There is no gaze verb; gaze is on-device (`gaze-and-attention.md`). The only
  cloud handle is a look-bearing tree, so `look=` selects from `vocab.GAZE_TREES` and
  nothing here invents a direction.
* **Pause.** `<break time="0.35s"/>` at an internal sentence boundary and after a leading
  interjection comma ("Hmm, ", "Oh, "). **Never after the final word.**
* **Rate limits** (the anti-twitch rules): <= 1 mood, <= 1 tree, <= 3 gestures per
  sentence, <= 6 per line, one `<usel>` span per sentence. A sentence under 6 words gets
  no talking gesture at all — only the terminal `Gesture_None`.

Invariants (asserted by sim/tests/test_automarkup.py)
-----------------------------------------------------
S1 **Idempotent.** Text that already carries markup (or any `<`) comes back unchanged.
S2 **The words never change.** `strip_markup(annotate(t)) == strip_markup(t)` for every
   input. The floor may add marks and spans; it may not add, drop, reorder or substitute
   a single spoken word. This is what makes it safe to turn on globally.
S3 **Per-chunk stable.** A streamed answer carries its mood on `chunk_index == 0` only,
   so a four-sentence answer no longer flips its face mid-answer; every chunk still ends
   with its own `Gesture_None`, and gesture spacing restarts per chunk.
S4 **Budget.** Stdlib only, no I/O, no model call, p95 < 1 ms on a 300-character line —
   it runs per spoken chunk, on the hot path between the first token and the first audio.

`MOXIE_AUTOMARKUP=0` turns the floor off and restores the previous behavior (a passthrough
at the seam, one mood + one gesture in `LLMApp`). See `enabled()`.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import List, Optional, Tuple

from . import vocab
from .segment import segment

__all__ = ["annotate", "enabled", "dropped_ids", "reset_dropped"]

# --------------------------------------------------------------------------- #
# Tunables — the anti-twitch numbers, all in one place
# --------------------------------------------------------------------------- #
BREAK_TIME = "0.35s"
TALK_EVERY = 5              # a talking gesture every N words (fixed; OpenMoxie rolls 3-7)
TALK_MIN_WORDS = 6          # a sentence shorter than this gets no talking gesture
TALK_TAIL = 2               # never place one inside the last N words of a sentence
TALK_PROBABILITY = 0.8      # OpenMoxie's 80 % roll, as a stable digest instead of a die
MAX_GESTURES_PER_SENTENCE = 3
MAX_GESTURES_PER_LINE = 6

#: Counts asset ids we refused to emit (an unknown hint, a cue that resolved to an id not
#: in the catalog). The corpus tests assert this stays 0 — a non-zero value means some
#: caller is feeding us vocabulary we cannot justify from our own evidence.
_DROPPED = 0


def dropped_ids() -> int:
    """How many unknown asset ids have been dropped since import (or `reset_dropped`)."""
    return _DROPPED


def reset_dropped() -> None:
    global _DROPPED
    _DROPPED = 0


def _drop(_what: str) -> None:
    global _DROPPED
    _DROPPED += 1


def enabled() -> bool:
    """False when `MOXIE_AUTOMARKUP` is 0/false/off/no — the one-variable rollback."""
    return os.environ.get("MOXIE_AUTOMARKUP", "1").strip().lower() not in (
        "0", "false", "off", "no")


# --------------------------------------------------------------------------- #
# Cue tables — ordered tuples, never sets, so nothing depends on hash order
# --------------------------------------------------------------------------- #
#: (regex, ePlaybackMood) in priority order. First match wins (§ "Mood" above).
_MOOD_CUES: Tuple[Tuple[re.Pattern, int], ...] = tuple((re.compile(p, re.I), m) for p, m in (
    (r"\b(sorry|apolog\w+|i feel sad|so sad|that is sad|that's sad|too bad|sad)\b", 2),
    (r"\b(oh|ooh|wow|whoa|woah|no way|guess what|really)\b", 5),
    (r"\b(oops|whoops|uh oh|uh-oh|my mistake|i messed up)\b", 4),
    (r"\b(hmm+|let me think|let me see|i wonder|not sure|maybe|good question)\b", 9),
    (r"\b(confus\w+|do not understand|don't understand|huh)\b", 8),
    (r"\b(amazing|awesome|great|wonderful|fantastic|brilliant|proud|well done|"
     r"good job|nice work|you did it|congratulations|hooray|yay|birthday|love|"
     r"excited|exciting|fun|happy|yes)\b", 1),
))
#: A question mark makes the line Curious, and an exclamation makes it Happy — checked
#: after the lexical cues so "Oh!" stays Surprised and "I'm sorry!" stays Sad.
_MOOD_QUESTION, _MOOD_EXCLAIM = 9, 1

#: Bumps intensity from 1 to 2 without an exclamation mark.
_EMPHATIC = re.compile(r"\b(so|really|very|super|totally|absolutely|such)\b", re.I)

#: Word classes that carry a gesture. Ordered: the first class that matches a word wins.
#: Their `AUTO_GESTURE_ME` / `AUTO_GESTURE_YOU` / `Gesture_We` / `Gesture_Small` /
#: `Gesture_Discard` are NOT here — those ids are not in our recovered catalog.
_WORD_GESTURES: Tuple[Tuple[frozenset, str], ...] = (
    (frozenset({"i", "me", "my", "mine", "myself", "we", "us", "our", "ours"}),
     "Gesture_Self"),
    (frozenset({"who", "what", "where", "when", "why", "how", "which", "please",
                "curious", "wondering", "question"}), "Gesture_Question"),
    (frozenset({"up", "above", "higher", "high", "wow", "great", "amazing", "awesome",
                "fantastic", "wonderful", "yay", "huge", "sky", "top"}), "Gesture_Higher"),
    (frozenset({"down", "below", "lower", "low", "little", "tiny", "small", "under",
                "ground"}), "Gesture_Lower"),
    (frozenset({"big", "enormous", "everything", "whole", "world", "everywhere"}),
     "Gesture_Large"),
    # "you" words point at the child. Deliberately no demonstratives ("that", "there"):
    # they are the most common words in a kid's sentence and would gesture on everything.
    (frozenset({"you", "your", "yours", "here"}), "Gesture_Point"),
)

#: Phrases that are praise, checked before the word classes so "You did it!" celebrates
#: rather than points.
_PRAISE = re.compile(
    r"\b(you did it|well done|good job|nice work|way to go|so proud|i am proud|"
    r"i'm proud|congratulations|hooray|great job|you got it)\b", re.I)

#: Line-level whole-body trees, in priority order: (regex, tree). One per line, at most.
_TREE_CUES: Tuple[Tuple[re.Pattern, str], ...] = tuple((re.compile(p, re.I), t) for p, t in (
    (r"\b(hmm+|let me think|let me see|i am thinking|i'm thinking|thinking about|"
     r"good question)\b", "Bht_Active_Thinking"),
    (r"\b(goodbye|bye|good night|goodnight|see you later|talk to you later|"
     r"sleep well|sweet dreams)\b", "Bht_Sign_off"),
    (r"\b(hello|hi there|hey there|welcome back|good morning|good afternoon|"
     r"good evening|nice to meet you)\b", "Bht_Gesture_Greet"),
))

#: Short openers that earn a pause when they lead a sentence and end in a comma.
_INTERJECTIONS = frozenset({
    "hmm", "hmmm", "hm", "oh", "ooh", "wow", "whoa", "woah", "well", "okay", "ok",
    "alright", "hey", "ah", "aha", "yes", "no", "sure", "right", "listen", "look",
    "guess what", "oh no", "uh oh", "you know",
})

#: Icon cues -> one of the four confirmed `icons-v2` values. Off unless `icons=True`:
#: all four are calendar/event assets, so emitting them from free chat would be guessing.
_ICON_CUES: Tuple[Tuple[re.Pattern, str], ...] = tuple((re.compile(p, re.I), v) for p, v in (
    (r"\bbirthday|birthdays\b", "Birthday"),
    (r"\b(school|class|classroom|teacher|homework)\b", "School"),
    (r"\b(doctor|dentist|medicine|appointment|check-?up|nurse)\b", "Medical"),
    (r"\b(family|mom|mum|dad|sister|brother|grandma|grandpa)\b",
     "Learning_About_Family_03_Heart_Family"),
))

_CLAUSE_SPLIT = re.compile(r"(?<=[,;:—–])\s+")
_CLOSERS = "\"')]}”’»"
_PUNCT = " \t\n\r.,;:!?—–…-" + _CLOSERS + "“‘«"


# --------------------------------------------------------------------------- #
# Determinism — a stable digest where OpenMoxie rolls a die
# --------------------------------------------------------------------------- #
def _ratio(turn_key: str, chunk_index: int, sentence_index: int, word_index: int) -> float:
    """A reproducible 0.0-1.0 from the chunk coordinates. blake2b, never `hash()`."""
    key = f"{turn_key}\x00{chunk_index}\x00{sentence_index}\x00{word_index}".encode("utf-8")
    digest = hashlib.blake2b(key, digest_size=4).digest()
    return int.from_bytes(digest, "big") / 4294967296.0


# --------------------------------------------------------------------------- #
# Scoring helpers
# --------------------------------------------------------------------------- #
def _bare(word: str) -> str:
    """A spoken word without its punctuation, lower-cased. `"Moxie."` -> `"moxie"`."""
    return word.strip(_PUNCT).lower()


def _stem(word: str) -> str:
    """`_bare`, with a contraction reduced to its subject: `"I'm"` -> `"i"`.

    Kids' speech is mostly contractions, and without this the self/you word classes miss
    "I'm", "I've", "you're", "we'll" — which is most of the lines that should gesture.
    """
    bare = _bare(word)
    for apostrophe in ("'", "\u2019"):
        if apostrophe in bare:
            return bare.split(apostrophe)[0]
    return bare


def _score_mood(text: str) -> Tuple[int, int]:
    """(mood, intensity) for a whole line — the rules in the module docstring."""
    mood = 0
    for pattern, value in _MOOD_CUES:
        if pattern.search(text):
            mood = value
            break
    else:
        if "?" in text:
            mood = _MOOD_QUESTION
        elif "!" in text:
            mood = _MOOD_EXCLAIM
    intensity = min(vocab.MAX_INTENSITY,
                    max(1, text.count("!") + len(_EMPHATIC.findall(text))))
    return mood, intensity


def _genre(sentence: str) -> Optional[str]:
    """The `<usel>` delivery for a sentence, from its terminal punctuation."""
    tail = sentence.rstrip().rstrip(_CLOSERS)
    if tail.endswith("?"):
        return "question"
    if tail.endswith("!"):
        return "excited"
    return None


def _clause_gesture(words: List[str], start: int, stop: int) -> Optional[Tuple[int, str]]:
    """(word index, `Gesture_*`) for one clause, or None if nothing in it carries."""
    clause = " ".join(words[start:stop])
    if _PRAISE.search(clause):
        return start, "Gesture_Celebrate"
    for i in range(start, stop):
        stem = _stem(words[i])
        if not stem:
            continue
        for members, gesture in _WORD_GESTURES:
            if stem in members:
                return i, gesture
    return None


def _clause_bounds(sentence: str, words: List[str]) -> List[Tuple[int, int]]:
    """Word-index spans of the sentence's clauses (split on , ; : em/en dash)."""
    bounds, at = [], 0
    for clause in _CLAUSE_SPLIT.split(sentence):
        n = len(clause.split())
        if n:
            bounds.append((at, at + n))
            at += n
    return bounds or [(0, len(words))]


# --------------------------------------------------------------------------- #
# The floor
# --------------------------------------------------------------------------- #
def annotate(text: str, *, mood_hint: Optional[str] = None,
             gesture_hint: Optional[str] = None, look: Optional[str] = None,
             turn_key: str = "", chunk_index: int = 0,
             icons: bool = False, sfx: bool = False, trees: bool = True) -> str:
    """One spoken line -> behavior markup. Pure: same inputs, same bytes, every time.

    `mood_hint` / `gesture_hint` are what the *model* chose, when the app knows (LLMApp's
    expressive JSON). A hint wins over the rules; an **unknown** hint is dropped and
    counted, never passed through to the wire.

    `look` names a look-bearing tree from `vocab.GAZE_TREES` — the only cloud-side handle
    on where Moxie looks, because there is no gaze verb.

    `turn_key` (the `event_id`) and `chunk_index` are the chunk bookkeeping that keeps a
    streamed answer stable: the mood is emitted on chunk 0 only, so an answer never flips
    its face mid-sentence, and gesture spacing restarts per chunk.

    `icons` / `sfx` are off by default — see the honest limits in the module docstring and
    in `vocab.ICON_VALUES` / `vocab.SFX_IDS`. `trees=False` suppresses the whole-body
    `Bht_*` cue for a caller that authored its own tree (the filler lines do).
    """
    if not text or not text.strip():
        return text
    # S1: never annotate anything that already carries markup — and, defensively, never
    # touch a line with an angle bracket in it, because a stray `<` would change how
    # `tts.strip_markup` tokenizes the result and could eat a spoken word.
    if "<" in text or ">" in text:
        return text

    sentences = [s for s in segment(text, min_chars=0) if s.strip()]
    if not sentences:
        return text

    mood, intensity = _score_mood(text)
    if mood_hint:
        hinted = vocab.MOOD_ALIASES.get(str(mood_hint).strip().lower())
        if hinted is None:
            _drop(f"mood_hint={mood_hint}")
        else:
            mood = hinted

    hint_gesture = None
    if gesture_hint:
        hinted_g = vocab.GESTURE_ALIASES.get(str(gesture_hint).strip().lower())
        if hinted_g is None:
            _drop(f"gesture_hint={gesture_hint}")
        elif hinted_g != "Gesture_None":
            hint_gesture = hinted_g

    # One whole-body tree per line, attached to the sentence whose text cued it.
    tree_at, tree_name = -1, None
    for pattern, name in (_TREE_CUES if trees else ()):
        for si, sentence in enumerate(sentences):
            if pattern.search(sentence):
                tree_at, tree_name = si, name
                break
        if tree_name:
            break
    if look:
        if look in vocab.GAZE_TREES:
            tree_at, tree_name = 0, look
        else:
            _drop(f"look={look}")

    icon_value = None
    if icons:
        for pattern, value in _ICON_CUES:
            if pattern.search(text):
                icon_value = value if value in vocab.ICON_SET else None
                if icon_value is None:
                    _drop(f"icon={value}")
                break

    # ---- build the token stream ------------------------------------------- #
    # ("m", mark) glues to whatever is next; ("w", word) is separated by one space from
    # the previous word; ("o"/"c", tag) opens/closes a <usel> span around a word group.
    tokens: List[Tuple[str, str]] = []
    if icon_value:
        tokens.append(("m", vocab.icons_mark([icon_value], command=vocab.ICON_SHOW)))
    if chunk_index == 0:
        # S3: exactly one mood mark per streamed answer, on the first chunk.
        tokens.append(("m", vocab.mood_mark(mood, intensity)))
    if sfx and mood == 1 and _PRAISE.search(text):
        # The only one of our two confirmed asset ids a spoken line should ever start:
        # the other is a looping music bed for a cast segment.
        tokens.append(("m", vocab.audio_mark(vocab.SFX_STINGER,
                                             channel=vocab.CHANNEL_STINGER)))

    emitted = 0                     # gestures emitted on this line (the <= 6 cap)
    for si, sentence in enumerate(sentences):
        if si:
            tokens.append(("m", vocab.break_mark(BREAK_TIME)))   # internal boundary only
        words = sentence.split()
        genre = _genre(sentence)
        has_tree = si == tree_at and tree_name is not None

        # -- which words carry a gesture, and where a clause pause goes --
        at: dict = {}               # word index -> gesture mark (placed BEFORE the word)
        after: dict = {}            # word index -> marks placed right AFTER the word
        per_sentence = 0
        bounds = _clause_bounds(sentence, words)

        if si == 0 and hint_gesture:
            at[0] = hint_gesture                 # the model's own choice wins
            per_sentence += 1
            emitted += 1
        elif not has_tree:
            # A sentence that plays a whole-body tree gets no arm gesture stacked on it.
            for (start, stop) in bounds:
                if (per_sentence >= MAX_GESTURES_PER_SENTENCE
                        or emitted >= MAX_GESTURES_PER_LINE):
                    break
                found = _clause_gesture(words, start, stop)
                if found and found[0] not in at:
                    at[found[0]] = found[1]
                    per_sentence += 1
                    emitted += 1

        # -- talking gestures: one every TALK_EVERY words, never near the closing pose --
        if not has_tree and len(words) >= TALK_MIN_WORDS:
            # Spacing restarts after the last carrying gesture, so the arm never fires
            # twice in a breath. With TALK_TAIL=2 the effective floor for a talking
            # gesture is an 8-word sentence.
            anchor = max(at) if at else 0
            pos = anchor + TALK_EVERY
            limit = len(words) - TALK_TAIL
            while pos < limit:
                if (per_sentence >= MAX_GESTURES_PER_SENTENCE
                        or emitted >= MAX_GESTURES_PER_LINE):
                    break
                if pos not in at and _ratio(turn_key, chunk_index, si, pos) < TALK_PROBABILITY:
                    at[pos] = "Gesture_Talk"
                    per_sentence += 1
                    emitted += 1
                pos += TALK_EVERY

        # -- a pause after a leading interjection comma ("Hmm, " / "Oh, ") --
        if len(bounds) > 1:
            first_stop = bounds[0][1]
            head = " ".join(words[:first_stop])
            if (head.rstrip().endswith(",") and _bare(head) in _INTERJECTIONS):
                after.setdefault(first_stop - 1, []).append(vocab.break_mark(BREAK_TIME))

        # -- lay the sentence down ------------------------------------------ #
        wrapped = genre is not None
        if wrapped:
            tokens.append(("o", f'<usel variant="{vocab.USEL_VARIANT}" genre="{genre}">'))
        for i, word in enumerate(words):
            if not wrapped and i in at:
                tokens.append(("m", vocab.tree_mark(at[i])))
            tokens.append(("w", word))
            for mark in after.get(i, ()):
                tokens.append(("m", mark))
        if wrapped:
            tokens.append(("c", "</usel>"))
            # A gesture may not be minted inside a span (that is how badly-nested tag
            # documents happen); a wrapped sentence plays its gestures right after it.
            for i in sorted(at):
                tokens.append(("m", vocab.tree_mark(at[i])))
        if has_tree:
            tokens.append(("m", vocab.tree_mark("Gesture_None", tree_name)))

    # Every chunk ends at rest: the robot may pause between spoken segments.
    tokens.append(("m", vocab.tree_mark("Gesture_None")))
    if icon_value:
        tokens.append(("m", vocab.icons_mark([], command=vocab.ICON_CLEAR)))

    # ---- render ------------------------------------------------------------ #
    out: List[str] = []
    space = False                   # a spoken word has been written; the next needs a gap
    for kind, value in tokens:
        if kind == "w":
            if space:
                out.append(" ")
            out.append(value)
            space = True
        elif kind == "o":
            if space:
                out.append(" ")
            out.append(value)
            space = False           # the first word inside the span opens it
        elif kind == "c":
            out.append(value)
            space = True
        else:
            out.append(value)
    return "".join(out)
