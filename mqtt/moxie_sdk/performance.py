"""
The behavior planner — score the line's *job*, stage a performance, render it once.

The markup floor (`automarkup.annotate`) maps **words** to tags. The planner scores what
the line is *doing* — its `RemoteDialog.DialogAct` — and stages a performance from that:
a `factual_question` holds its gaze and tilts, an `apology` goes quiet, `appreciation`
celebrates, `backchannelling` ("mm-hm") gets **no arm gesture at all**.

Three rules:
1. **The planner does not emit strings.** `plan()` returns a `Performance`; `render()` is
   the *only* function that mints a mark, so validation is total and goldens are JSON.
2. **A brain may suggest, never authorize.** Every id — rule-chosen or from a model via
   `ctx` — passes the same `validate()` against the frozen catalog in `vocab.py`; unknown
   ids are **dropped and counted** (the codebase's positive-list rule).
3. **Always degrade to the floor.** `plan()` returns `None` rather than raising, and the
   seam (`supervisor/markup.py`) falls back to `annotate()` on `None` or any exception.

Deterministic: no `random`, clock, network or model call. Talking-gesture spacing reuses
the floor's `blake2b` digest.

A `Beat` is one run of words performed in one state: sentences sub-split at clause
punctuation and at the talking-gesture stride, so every mark falls on a beat boundary.

Honest limits:
* **No gaze verb** exists among the 24 recovered markup commands; gaze lives on the robot
  (`gaze-and-attention.md`). The only handle is choosing a look-bearing tree, so
  `Beat.gaze` is a closed enum over `vocab.GAZE_TREES`. We **cannot lower the gaze**; an
  apology gets the least-searching tree (`Bht_Idle_Listening`).
* **No nod id** either: `backchannelling` renders as "no arm gesture, attentive tree".
* **`timeout` is a turn state**, reachable only via `ctx={"timed_out": True}`.
* **The act classifier is a rule engine** over cue phrases and sentence shape; a learned
  classifier is P2 (`backlog/expressiveness.md` §2.7).
* **No hardware has played our markup**; the browser SIM is the only renderer we test.

Prior art: OpenMoxie (MIT) `site/hive/automarkup/` informed the floor's behaviors
(`backlog/expressiveness.md` §1.4). No code or data table was copied. Sources for every
id are cited in `vocab.py`.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import vocab
from .segment import segment
# Reuse the floor's recovered rules rather than a divergent copy.
from .automarkup import (
    BREAK_TIME, TALK_EVERY, TALK_MIN_WORDS, TALK_TAIL, TALK_PROBABILITY,
    MAX_GESTURES_PER_LINE, MAX_GESTURES_PER_SENTENCE,
    _CLAUSE_SPLIT, _ICON_CUES, _INTERJECTIONS, _PRAISE,
    _bare, _clause_gesture, _genre, _ratio, _score_mood,
)

__all__ = [
    "Beat", "Performance", "plan", "validate", "render", "classify",
    "to_json", "from_json", "dropped_ids", "reset_dropped", "ACT_PROFILES",
    "MAX_PLAN_CHARS", "MAX_MOOD_MARKS",
]

# --------------------------------------------------------------------------- #
# Budget guards — a planner may never make a turn worse
# --------------------------------------------------------------------------- #
#: Longer than this and `plan()` declines so the floor answers — bounds hot-path work.
MAX_PLAN_CHARS = 2000
#: At most this many `cmd:playback-mood` marks per line: the initial mood plus one
#: transition (`backlog/expressiveness.md` §2.5) — no twitchy face.
MAX_MOOD_MARKS = 2
#: Beats past this are performed as plain text. Bounds `render()` on a pathological line.
MAX_BEATS = 96

#: Counts ids `validate()` refused; the corpus test asserts 0 for our own output.
_DROPPED = 0


def dropped_ids() -> int:
    """How many unknown ids `validate()` has dropped since import (or `reset_dropped`)."""
    return _DROPPED


def reset_dropped() -> None:
    global _DROPPED
    _DROPPED = 0


def _drop(bad: List[str], what: str) -> None:
    global _DROPPED
    _DROPPED += 1
    bad.append(what)


# --------------------------------------------------------------------------- #
# The structure
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Beat:
    """One run of words performed in one state. Every slot is a closed `vocab.py`
    vocabulary, enforced by `validate()`."""
    text: str
    mood: Optional[int] = None          # ePlaybackMood 0-10; None = keep the current face
    mood_intensity: int = 0             # 0-2 (`maxIntensity=2`)
    gesture: Optional[str] = None       # one of the 12 `Gesture_*`
    tree: Optional[str] = None          # one of the named `Bht_*` (whole body)
    gaze: Optional[str] = None          # a look-bearing tree — see the module docstring
    icon: Optional[str] = None          # `icons-v2` value (4 confirmed)
    sfx: Optional[str] = None           # `SoundToPlay` id (2 confirmed)
    spurt: Optional[str] = None         # one of the 52 vocal gestures
    usel: Optional[str] = None          # none|question|motivational|intimate|excited
    break_after: Optional[float] = None  # seconds of `<break>` after this beat


@dataclass(frozen=True)
class Performance:
    """A whole staged line: its beats plus the scored output the wire carries.

    `mood`/`mood_intensity` are the **line's** score (`RemoteChatOutput.mood`); a beat's
    `mood` is a mid-line face change. `dropped` lists ids `validate()` removed (the
    preview console shows them).
    """
    beats: Tuple[Beat, ...] = ()
    dialog_act: Optional[str] = None    # one of the 22 `RemoteDialog.DialogAct`
    emotion: Optional[str] = None       # one of the 7 `RemoteDialog.EmotionState`
    signal: Optional[str] = None        # one of the 9 `RemoteSignals.Signal`
    mood: Optional[int] = None          # the line's ePlaybackMood (the wire field)
    mood_intensity: int = 0             # 0-2
    dropped: Tuple[str, ...] = ()       # ids validate() refused, as "slot=id"

    @property
    def text(self) -> str:
        """The spoken words, exactly as they will be rendered."""
        return " ".join(b.text for b in self.beats if b.text)

    def scored(self) -> Dict[str, Any]:
        """The scored output fields for `build_chat_response` (ai-seam.md §②). `mood`
        goes out as the **name** (the wire field is a label; `cmd:playback-mood` takes the int)."""
        out: Dict[str, Any] = {}
        if self.mood is not None:
            out["mood"] = vocab.MOOD_NAME_BY_ID.get(self.mood)
            out["mood_intensity"] = int(self.mood_intensity)
        if self.dialog_act:
            out["dialog_act"] = self.dialog_act
        if self.emotion:
            out["emotion"] = self.emotion
        if self.signal:
            out["signal"] = self.signal
        return out


# --------------------------------------------------------------------------- #
# The act -> performance table
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Profile:
    """How one dialog act is performed. `mood=None` means "let the words decide"."""
    mood: Optional[int] = None
    intensity: Optional[int] = None
    gesture: Optional[str] = None       # the act's signature gesture, on the first beat
    no_gesture: bool = False            # this act is performed WITHOUT arm gestures
    tree: Optional[str] = None          # a whole-body tree for the line (at most one)
    gaze: Optional[str] = None          # a look-bearing tree, when no `tree` is set
    signal: str = "no_signal"


#: One row per `RemoteDialog.DialogAct` (22) — remote-chat-protocol.md:119-122.
#: Every id here is checked against `vocab` by `validate()`; the table is not trusted.
ACT_PROFILES: Dict[str, _Profile] = {
    # -- openers and closers: the two acts that earn a whole-body tree ---------
    "opening": _Profile(mood=vocab.MOODS["happy"], tree="Bht_Gesture_Greet",
                        signal="no_signal"),
    "closing": _Profile(mood=vocab.MOODS["happy"], tree="Bht_Sign_off",
                        signal="closing"),
    # -- repair ---------------------------------------------------------------
    "apology": _Profile(mood=vocab.MOODS["sad"], intensity=1, gesture="Gesture_Self",
                        # No id lowers the gaze (see the module docstring).
                        gaze="Bht_Idle_Listening", signal="apology"),
    "apology_response": _Profile(mood=vocab.MOODS["happy"], intensity=1,
                                 gesture="Gesture_Point", gaze="Bht_Idle_Near_Focused",
                                 signal="confirmation_agreement"),
    # -- warmth ---------------------------------------------------------------
    "appreciation": _Profile(mood=vocab.MOODS["happy"], intensity=2,
                             gesture="Gesture_Celebrate", gaze="Bht_Idle_Near_Focused",
                             signal="confirmation_agreement"),
    "thanking": _Profile(mood=vocab.MOODS["happy"], intensity=1, gesture="Gesture_Point",
                         gaze="Bht_Idle_Near_Focused", signal="confirmation_agreement"),
    # -- listening: the act that is performed by NOT moving the arms ----------
    "backchannelling": _Profile(mood=vocab.MOODS["neutral"], no_gesture=True,
                                gaze="Bht_Idle_Listening", signal="interest"),
    "hold": _Profile(mood=vocab.MOODS["curious"], no_gesture=True,
                     tree="Bht_Active_Thinking", signal="no_signal"),
    # -- questions: hold the gaze, tilt the head ------------------------------
    "factual_question": _Profile(mood=vocab.MOODS["curious"], gesture="Gesture_Question",
                                 gaze="Bht_Idle_Near_Focused", signal="interest"),
    "opinion_question": _Profile(mood=vocab.MOODS["curious"], gesture="Gesture_Question",
                                 gaze="Bht_Idle_Curious", signal="interest"),
    "yes_no_question": _Profile(mood=vocab.MOODS["curious"], gesture="Gesture_Question",
                                gaze="Bht_Idle_Near_Focused", signal="interest"),
    # -- answers --------------------------------------------------------------
    "pos_answer": _Profile(mood=vocab.MOODS["happy"], intensity=1, no_gesture=True,
                           gaze="Bht_Idle_Near_Focused",
                           signal="confirmation_agreement"),
    "neg_answer": _Profile(mood=vocab.MOODS["neutral"], gesture="Gesture_Lower",
                           gaze="Bht_Idle_Near_Focused",
                           signal="rejection_disagreement"),
    "other_answers": _Profile(mood=vocab.MOODS["curious"], gesture="Gesture_Think",
                              gaze="Bht_Idle_Curious", signal="no_signal"),
    # -- stance ---------------------------------------------------------------
    "opinion": _Profile(gesture="Gesture_Self", gaze="Bht_Idle_Curious",
                        signal="interest"),
    "complaint": _Profile(mood=vocab.MOODS["concerned"], gesture="Gesture_Lower",
                          gaze="Bht_Idle_Listening", signal="complaint_clarification"),
    "comment": _Profile(mood=vocab.MOODS["surprised"], gaze="Bht_Idle_Curious",
                        signal="interest"),
    "command": _Profile(gesture="Gesture_Point", gaze="Bht_Idle_Near_Focused",
                        signal="no_signal"),
    # -- the line went nowhere ------------------------------------------------
    "abandon": _Profile(mood=vocab.MOODS["shy"], no_gesture=True, gaze="Bht_Search",
                        signal="non_interest"),
    "timeout": _Profile(mood=vocab.MOODS["curious"], no_gesture=True, gaze="Bht_Search",
                        signal="non_interest"),
    # -- the defaults: let the floor's word rules do the work -----------------
    "statement_non_opinion": _Profile(),
    "other": _Profile(),
}

#: ePlaybackMood -> `RemoteDialog.EmotionState` (remote-chat-protocol.md:123), a different
#: enum from the face. Moods with no honest counterpart map to neutral.
_EMOTION_BY_MOOD: Dict[int, str] = {
    vocab.MOODS["neutral"]: "neutral", vocab.MOODS["happy"]: "joy",
    vocab.MOODS["sad"]: "sadness", vocab.MOODS["angry"]: "anger",
    vocab.MOODS["surprised"]: "surprise", vocab.MOODS["afraid"]: "fear",
}


# --------------------------------------------------------------------------- #
# The act classifier — cue phrases and sentence shape, in priority order
# --------------------------------------------------------------------------- #
def _rx(pattern: str) -> "re.Pattern":
    return re.compile(pattern, re.I)


#: Whole-line shapes, tried first: a short line that IS one of these acts entirely.
_ACT_WHOLE: Tuple[Tuple[str, "re.Pattern"], ...] = (
    ("backchannelling", _rx(r"^(m+h+m+|mm+-?h?m*|uh[- ]?huh|i see|go on|mhm|gotcha|"
                            r"right|okay|ok)[.!]?$")),
    ("pos_answer", _rx(r"^(yes|yeah|yep|yup|sure|of course|absolutely|definitely|"
                       r"sure thing|you bet)[.!]?$")),
    ("neg_answer", _rx(r"^(no|nope|nah|not really|not quite)[.!]?$")),
    ("comment", _rx(r"^(wow|whoa|woah|cool|neat|nice|awesome|amazing)[.!]?$")),
)

#: Cue phrases anywhere in the line, in priority order. First match wins.
_ACT_CUES: Tuple[Tuple[str, "re.Pattern"], ...] = (
    # Repair before warmth: "sorry, thank you for waiting" is an apology.
    ("apology_response", _rx(r"\b(that('s| is) (ok|okay|alright|all right|fine)|"
                             r"no worries|do ?n[o']?t worry about it|it('s| is) ok(ay)?|"
                             r"no problem|that happens to everyone)\b")),
    ("apology", _rx(r"\b(i am sorry|i'm sorry|i apolog\w+|my apologies|sorry about|"
                    r"oops|whoops|my mistake|i messed up|that was my fault)\b")),
    ("thanking", _rx(r"\b(thank you|thanks|thank u|i appreciate (that|it|you))\b")),
    # Praise aimed at the child — the same phrase table the floor celebrates on.
    ("appreciation", _PRAISE),
    ("closing", _rx(r"\b(goodbye|bye for now|bye bye|bye|good ?night|see you later|"
                    r"talk to you later|sleep well|sweet dreams|until next time)\b")),
    ("opening", _rx(r"\b(hello|hi there|hey there|welcome back|good morning|"
                    r"good afternoon|good evening|nice to meet you)\b")),
    ("hold", _rx(r"\b(hold on|one moment|just a (second|moment|minute)|let me think|"
                 r"let me see|give me a second|bear with me)\b")),
    ("abandon", _rx(r"\b(never ?mind|forget it|let('s| us) drop it|scratch that)\b")),
    ("complaint", _rx(r"\b(that('s| is) not fair|i do ?n[o']?t like|this is broken|"
                      r"is ?n[o']?t working|too hard|that('s| is) too much)\b")),
    ("other_answers", _rx(r"^(maybe|perhaps|i do ?n[o']?t know|i am not sure|"
                          r"i'm not sure|it depends)\b")),
    ("opinion", _rx(r"\b(i think|i believe|in my opinion|i feel like|my favou?rite|"
                    r"i love|i really like|if you ask me)\b")),
    ("comment", _rx(r"\b(that('s| is)|this is) (so |really |very |pretty )?"
                    r"(cool|neat|interesting|funny|amazing|wild|surprising)\b")),
)

#: Question shapes, tried only on a line that actually ends in `?`.
_Q_OPINION = _rx(r"\b(what do you think|do you (like|prefer|enjoy)|how do you feel|"
                 r"what (is|are) your (favou?rite|best)|would you rather|"
                 r"which do you like)\b")
_Q_YES_NO = _rx(r"^(do|does|did|are|is|was|were|can|could|will|would|have|has|had|"
                r"should|shall|may|might|am)\b")
_Q_FACTUAL = _rx(r"^(what|where|when|who|whose|whom|which|how|why)\b")

#: An imperative opener — the `command` act. Kept short so declaratives are not swallowed.
_COMMAND = _rx(r"^(tell|show|say|try|look|listen|come|let'?s|let us|close|open|put|take|"
               r"pick|give|find|guess|touch|press|hold|stop|start|go|sit|stand|clap|"
               r"wave|repeat|point|count|imagine|pretend|draw|sing|read|watch|help)\b")

_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def classify(text: str, *, ctx: Optional[dict] = None) -> str:
    """Which of the 22 `RemoteDialog.DialogAct`s this line performs.

    A brain's `ctx["dialog_act"]` wins only if it is one of the 22; otherwise the rules
    decide. `ctx={"timed_out": True}` is the only way to reach `timeout`.
    """
    ctx = ctx or {}
    hint = ctx.get("dialog_act")
    if hint and str(hint) in vocab.DIALOG_ACTS:
        return str(hint)
    if ctx.get("timed_out"):
        return "timeout"
    line = (text or "").strip()
    if not line:
        return "timeout" if ctx.get("timed_out") else "other"
    if not _HAS_LETTER.search(line):
        return "other"                       # "..." / "!!!" — nothing to classify

    flat = re.sub(r"\s+", " ", line)
    for act, pattern in _ACT_WHOLE:
        if pattern.match(flat):
            return act
    for act, pattern in _ACT_CUES:
        if pattern.search(flat):
            return act
    if flat.rstrip().endswith("?"):
        if _Q_OPINION.search(flat):
            return "opinion_question"
        if _Q_FACTUAL.match(flat):
            return "factual_question"
        if _Q_YES_NO.match(flat):
            return "yes_no_question"
        return "factual_question"            # a question we cannot shape is still one
    if _COMMAND.match(flat):
        return "command"
    return "statement_non_opinion"


# --------------------------------------------------------------------------- #
# plan — the scoring pass
# --------------------------------------------------------------------------- #
def _runs(sentence: str) -> List[List[str]]:
    """One sentence -> its clause runs, as word lists. `[]` for a sentence with no words."""
    out: List[List[str]] = []
    for clause in _CLAUSE_SPLIT.split(sentence):
        words = clause.split()
        if words:
            out.append(words)
    if not out:
        words = sentence.split()
        if words:
            out.append(words)
    return out


def _talk_positions(words: Sequence[str], anchor: int, turn_key: str,
                    chunk_index: int, si: int) -> List[int]:
    """Word indices where a `Gesture_Talk` falls, by the floor's spacing rules: every
    `TALK_EVERY` words after the carrying gesture, never in the last `TALK_TAIL`, gated by
    the floor's stable digest."""
    if len(words) < TALK_MIN_WORDS:
        return []
    hits, pos, limit = [], anchor + TALK_EVERY, len(words) - TALK_TAIL
    while pos < limit:
        if _ratio(turn_key, chunk_index, si, pos) < TALK_PROBABILITY:
            hits.append(pos)
        pos += TALK_EVERY
    return hits


def plan(text: str, *, ctx: Optional[dict] = None) -> Optional[Performance]:
    """Score one spoken line into a `Performance`. `None` means "let the floor answer".

    Pure and deterministic. `ctx` keys, all optional *hints* (validated, never trusted):
      `dialog_act`, `mood` (name/alias or 0-10 int), `gesture`, `emotion`, `signal`,
      `intensity` (0-2), `look` (a `vocab.GAZE_TREES` id), `timed_out` (bool),
      `icons` / `sfx` (bool, both off by default — `vocab` records why),
      `turn_key` + `chunk_index` (chunk bookkeeping: a chunk past the first plans **no**
      mood, so a streamed answer holds one face instead of flipping it every sentence).

    Returns `None` — never raises — for empty text, text already carrying markup (S1:
    an authored line is not ours to restage), and text past `MAX_PLAN_CHARS`.
    """
    ctx = dict(ctx or {})
    if not text or not text.strip():
        return None
    # S1 idempotence; a stray `<` would also change how `tts.strip_markup` tokenizes.
    if "<" in text or ">" in text:
        return None
    if len(text) > MAX_PLAN_CHARS:
        return None

    sentences = [s for s in segment(text, min_chars=0) if s.strip()]
    if not sentences:
        return None

    turn_key = str(ctx.get("turn_key") or "")
    try:
        chunk_index = int(ctx.get("chunk_index") or 0)
    except (TypeError, ValueError):
        chunk_index = 0

    act = classify(text, ctx=ctx)
    profile = ACT_PROFILES.get(act, ACT_PROFILES["other"])

    # ---- the line's mood: a hint wins, then the WORDS, then the act --------- #
    # Words beat the act: the floor's mood cues are recovered evidence
    # (behavior-markup.md:117-127); the act profile only fills lines that score Neutral.
    rule_mood, rule_strength = _score_mood(text)
    mood = rule_mood if rule_mood else (profile.mood if profile.mood is not None else 0)
    strength = profile.intensity if profile.intensity is not None else rule_strength
    hint_mood = ctx.get("mood")
    if hint_mood is not None and hint_mood != "":
        if isinstance(hint_mood, bool):
            resolved = None                  # a bool is an int in Python; refuse it
        elif isinstance(hint_mood, int):
            resolved = hint_mood if hint_mood in vocab.MOOD_IDS else None
        else:
            resolved = vocab.MOOD_ALIASES.get(str(hint_mood).strip().lower())
        if resolved is not None:
            mood = resolved
        # An unrecognized hint falls through to the rules.
    if ctx.get("intensity") is not None:
        try:
            strength = max(0, min(vocab.MAX_INTENSITY, int(ctx["intensity"])))
        except (TypeError, ValueError):
            pass
    strength = max(0, min(vocab.MAX_INTENSITY, int(strength)))

    hint_gesture = None
    if ctx.get("gesture"):
        hint_gesture = vocab.GESTURE_ALIASES.get(str(ctx["gesture"]).strip().lower())
        if hint_gesture == "Gesture_None":
            hint_gesture = None

    # ---- the line's whole-body tree, and the gaze it displaces -------------- #
    tree = profile.tree
    gaze = None if tree else (profile.gaze or None)
    look = ctx.get("look")
    if look and str(look) in vocab.GAZE_TREES:
        gaze, tree = (str(look), None)       # an explicit look overrides both

    # ---- icons / sfx: gated off, exactly as in the floor -------------------- #
    # Either an explicit id (`Reply.icon`/`Reply.sfx`) or the boolean gate that lets cue
    # rules pick one; off by default (see `vocab.ICON_VALUES` / `vocab.SFX_IDS` for why).
    icon = None
    if isinstance(ctx.get("icon"), str) and ctx["icon"] in vocab.ICON_SET:
        icon = ctx["icon"]
    elif ctx.get("icons"):
        for pattern, value in _ICON_CUES:
            if pattern.search(text):
                icon = value
                break
    sfx = None
    if isinstance(ctx.get("sfx"), str) and ctx["sfx"] in vocab.SFX_SET:
        sfx = ctx["sfx"]
    elif ctx.get("sfx") and act == "appreciation" and mood == vocab.MOODS["happy"]:
        sfx = vocab.SFX_STINGER

    # ---- lay out the beats -------------------------------------------------- #
    beats: List[Beat] = []
    emitted_gestures = 0
    moods_used = 0
    last_mood: Optional[int] = None
    placed_act_gesture = False

    for si, sentence in enumerate(sentences):
        clauses = _runs(sentence)
        if not clauses:
            continue
        genre = _genre(sentence)
        per_sentence = 0
        last_clause = len(clauses) - 1
        # A sentence that plays the whole-body tree gets no arm gesture stacked on it.
        sentence_has_tree = tree is not None and si == 0

        for ci, words in enumerate(clauses):
            # -- which word in this clause carries a gesture -------------------
            carry_at, carry = None, None
            if not profile.no_gesture and not sentence_has_tree:
                if hint_gesture and not placed_act_gesture:
                    carry_at, carry = 0, hint_gesture
                elif (profile.gesture and not placed_act_gesture
                        and si == 0 and ci == 0):
                    carry_at, carry = 0, profile.gesture
                else:
                    found = _clause_gesture(words, 0, len(words))
                    if found:
                        carry_at, carry = found
            if carry is not None:
                if (per_sentence >= MAX_GESTURES_PER_SENTENCE
                        or emitted_gestures >= MAX_GESTURES_PER_LINE):
                    carry_at, carry = None, None
                else:
                    per_sentence += 1
                    emitted_gestures += 1
                    placed_act_gesture = True

            # -- talking gestures become their own beats -----------------------
            talk = ([] if (profile.no_gesture or sentence_has_tree)
                    else _talk_positions(words, carry_at or 0, turn_key, chunk_index, si))
            cuts = sorted({0} | {p for p in talk if p > (carry_at or 0)})
            # -- the mood this clause performs ---------------------------------
            clause_mood = None
            if chunk_index == 0 and moods_used < MAX_MOOD_MARKS:
                candidate = mood if last_mood is None else _clause_mood(
                    " ".join(words), mood, last_mood)
                if candidate is not None and candidate != last_mood:
                    clause_mood, last_mood = candidate, candidate
                    moods_used += 1

            # -- the pause after this clause -----------------------------------
            brk = None
            head = " ".join(words)
            if (ci == 0 and len(clauses) > 1 and head.rstrip().endswith(",")
                    and _bare(head) in _INTERJECTIONS):
                brk = float(BREAK_TIME.rstrip("s"))
            if ci == last_clause and si < len(sentences) - 1:
                brk = float(BREAK_TIME.rstrip("s"))   # internal sentence boundary only

            for k, cut in enumerate(cuts):
                stop = cuts[k + 1] if k + 1 < len(cuts) else len(words)
                if cut >= stop:
                    continue
                is_first = (k == 0)
                is_last = (k == len(cuts) - 1)
                g = None
                if carry is not None and carry_at is not None and cut <= carry_at < stop:
                    g = carry
                elif cut in talk and not is_first:
                    if (per_sentence < MAX_GESTURES_PER_SENTENCE
                            and emitted_gestures < MAX_GESTURES_PER_LINE):
                        g = "Gesture_Talk"
                        per_sentence += 1
                        emitted_gestures += 1
                beats.append(Beat(
                    text=" ".join(words[cut:stop]),
                    mood=clause_mood if is_first else None,
                    mood_intensity=strength if is_first and clause_mood is not None else 0,
                    gesture=g,
                    tree=tree if (is_first and si == 0 and ci == 0) else None,
                    gaze=gaze if (is_first and si == 0 and ci == 0) else None,
                    icon=icon if (is_first and si == 0 and ci == 0) else None,
                    sfx=sfx if (is_first and si == 0 and ci == 0) else None,
                    usel=genre if (ci == last_clause and is_last) else None,
                    break_after=brk if is_last else None,
                ))
                if len(beats) >= MAX_BEATS:
                    break
            if len(beats) >= MAX_BEATS:
                break
        if len(beats) >= MAX_BEATS:
            break

    if not beats:
        return None
    # A hint is taken only when it is in the catalog; otherwise the rules answer (an
    # unhonorable suggestion must not blank the field).
    emotion = str(ctx.get("emotion") or "")
    signal = str(ctx.get("signal") or "")
    return Performance(
        beats=tuple(beats), dialog_act=act,
        emotion=(emotion if emotion in vocab.EMOTION_STATES
                 else _EMOTION_BY_MOOD.get(mood, "neutral")),
        signal=(signal if signal in vocab.SIGNALS else profile.signal),
        mood=mood, mood_intensity=strength,
    )


def _clause_mood(clause: str, line_mood: int, current: int) -> Optional[int]:
    """A clause's own mood, or None to hold the line's face (§2.1, bounded by
    `MAX_MOOD_MARKS`): changes only when the clause's own words score a different mood."""
    scored, _ = _score_mood(clause)
    if scored and scored != current:
        return scored
    return None


# --------------------------------------------------------------------------- #
# validate — the positive list, applied to every id whatever chose it
# --------------------------------------------------------------------------- #
def _check(value, catalog, slot: str, bad: List[str]):
    """`value` if it is in `catalog`, else None with the drop recorded."""
    if value is None or value == "":
        return None
    if value in catalog:
        return value
    _drop(bad, f"{slot}={value}")
    return None


def validate(p: Optional[Performance], *, strict: bool = False) -> Optional[Performance]:
    """Every id in `p` checked against the frozen catalog in `vocab.py` — the single gate
    for rule- and model-chosen ids alike.

    **Drops, does not raise**: an unjustified id is removed, recorded in
    `Performance.dropped` and counted, costing a gesture rather than a turn.
    `strict=True` raises instead (the property test).
    """
    if p is None:
        return None
    bad: List[str] = list(p.dropped)
    beats: List[Beat] = []
    for b in p.beats:
        mood = b.mood
        # `bool` is an `int`: `True` would render as mood 1 but serialize as `true`.
        if isinstance(mood, bool) or (mood is not None and mood not in vocab.MOOD_IDS):
            _drop(bad, f"mood={mood}")
            mood = None
        try:
            strength = int(b.mood_intensity)
        except (TypeError, ValueError):
            _drop(bad, f"intensity={b.mood_intensity!r}")
            strength = 0
        if not 0 <= strength <= vocab.MAX_INTENSITY:
            _drop(bad, f"intensity={strength}")
            strength = max(0, min(vocab.MAX_INTENSITY, strength))
        brk = b.break_after
        if brk is not None:
            try:
                brk = float(brk)
            except (TypeError, ValueError):
                _drop(bad, f"break_after={b.break_after!r}")
                brk = None
            else:
                if not 0.0 < brk <= 5.0:
                    _drop(bad, f"break_after={brk}")
                    brk = None
        beats.append(Beat(
            text=b.text,
            mood=mood, mood_intensity=strength,
            gesture=_check(b.gesture, vocab.GESTURE_SET, "gesture", bad),
            tree=_check(b.tree, vocab.TREE_SET, "tree", bad),
            gaze=_check(b.gaze, vocab.GAZE_TREES, "gaze", bad),
            icon=_check(b.icon, vocab.ICON_SET, "icon", bad),
            sfx=_check(b.sfx, vocab.SFX_SET, "sfx", bad),
            spurt=_check(b.spurt, vocab.SPURT_SET, "spurt", bad),
            usel=_check(b.usel, vocab.USEL_GENRE_SET, "usel", bad),
            break_after=brk,
        ))
    mood = p.mood
    if isinstance(mood, bool) or (mood is not None and mood not in vocab.MOOD_IDS):
        _drop(bad, f"mood={mood}")
        mood = None
    out = Performance(
        beats=tuple(beats),
        dialog_act=_check(p.dialog_act, vocab.DIALOG_ACTS, "dialog_act", bad),
        emotion=_check(p.emotion, vocab.EMOTION_STATES, "emotion", bad),
        signal=_check(p.signal, vocab.SIGNALS, "signal", bad),
        mood=mood,
        mood_intensity=max(0, min(vocab.MAX_INTENSITY, int(p.mood_intensity or 0))),
        dropped=tuple(bad),
    )
    if strict and bad:
        raise ValueError("unknown ids in Performance: " + ", ".join(bad))
    return out


# --------------------------------------------------------------------------- #
# render — the ONLY place a mark is minted
# --------------------------------------------------------------------------- #
def render(p: Optional[Performance]) -> str:
    """A validated `Performance` -> behavior markup. The one string-producing function.

    Per beat: screen, face, sound, whole body, arm, words (in their `<usel>`), pause —
    the body moves with the line, not after it (unlike the floor). Every line ends with a
    derived `Gesture_None` (back to rest) and an `icons-v2` clear if it showed one.
    """
    if p is None or not p.beats:
        return ""
    out: List[str] = []
    space = False
    showed_icon = False
    for b in p.beats:
        if b.icon:
            out.append(vocab.icons_mark([b.icon], command=vocab.ICON_SHOW))
            showed_icon = True
        if b.mood is not None:
            out.append(vocab.mood_mark(b.mood, b.mood_intensity))
        if b.sfx:
            out.append(vocab.audio_mark(b.sfx, channel=vocab.CHANNEL_STINGER))
        if b.spurt:
            out.append(vocab.mark("vocal-gesture", {"spurt_id": b.spurt}))
        if b.tree:
            out.append(vocab.tree_mark("Gesture_None", b.tree))
        elif b.gaze:
            out.append(vocab.tree_mark("Gesture_None", b.gaze))
        if b.gesture:
            out.append(vocab.tree_mark(b.gesture))
        if b.text:
            if space:
                out.append(" ")
            if b.usel:
                out.append(vocab.usel(b.text, b.usel))
            else:
                out.append(b.text)
            space = True
        if b.break_after:
            out.append(vocab.break_mark(f"{b.break_after:g}s"))
    out.append(vocab.tree_mark("Gesture_None"))
    if showed_icon:
        out.append(vocab.icons_mark([], command=vocab.ICON_CLEAR))
    return "".join(out)


# --------------------------------------------------------------------------- #
# JSON — goldens, and the preview console's panel
# --------------------------------------------------------------------------- #
def to_json(p: Optional[Performance]) -> Optional[dict]:
    """A `Performance` as plain JSON (goldens, preview). Empty slots are omitted."""
    if p is None:
        return None
    beats = []
    for b in p.beats:
        row = {k: v for k, v in asdict(b).items()
               if v is not None and not (k == "mood_intensity" and not v)}
        beats.append(row)
    out: Dict[str, Any] = {"beats": beats}
    for key in ("dialog_act", "emotion", "signal", "mood"):
        value = getattr(p, key)
        if value is not None:
            out[key] = value
    if p.mood_intensity:
        out["mood_intensity"] = p.mood_intensity
    if p.dropped:
        out["dropped"] = list(p.dropped)
    return out


_BEAT_FIELDS = tuple(f for f in Beat.__dataclass_fields__)          # noqa: SLF001


def from_json(data: Optional[dict]) -> Optional[Performance]:
    """The inverse of `to_json`. Unknown keys are ignored; `validate()` decides what
    may be performed."""
    if not data:
        return None
    beats = tuple(
        Beat(**{k: v for k, v in (row or {}).items() if k in _BEAT_FIELDS})
        for row in (data.get("beats") or ())
    )
    return Performance(
        beats=beats,
        dialog_act=data.get("dialog_act"), emotion=data.get("emotion"),
        signal=data.get("signal"), mood=data.get("mood"),
        mood_intensity=int(data.get("mood_intensity") or 0),
        dropped=tuple(data.get("dropped") or ()),
    )
