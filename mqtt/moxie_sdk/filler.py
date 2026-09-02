"""
Filler lines — what Moxie says while a slow brain is still thinking.

The robot re-prompts if the cloud stays quiet for roughly **20 s**
(docs/architecture/openmoxie-feature-audit.md:347), and a measured live gateway turn
costs 45 s healthy / 18 s degraded (docs/architecture/implementation-plan.md:138). A
brain slower than the window leaves a child listening to silence, so the runtime speaks
one of these lines first — `RemoteChatResponse{result: REPLY_PENDING}` chunk 0, "more
chunks to come" (RemoteChat.proto ResultCode 9;
docs/reverse-engineering/protocol/remote-chat-protocol.md:63) — and delivers the real
answer as chunk 1.

Idea credit: OpenMoxie Fork A's `ReasoningChatSession` (MIT) runs a long inference on a
thread pool and speaks rotating interludes meanwhile, never repeating the last one. The
pattern is theirs; these lines, this markup and the multi-chunk wire shape are ours.

Every line ships with **behavior markup**, so the filler is performed rather than read:
a `playback-mood` mark plus a thinking `behaviour-tree`
(docs/reverse-engineering/runtime/behavior-markup.md — mood enum §"playback-mood",
trees `Bht_Active_Thinking` / `Bht_Idle_Curious` / `Bht_Vg_hmm_thinking`, gestures
`Gesture_Think` / `Gesture_Think_Subtle` / `Gesture_Question`). `moxie_sdk.tts.strip_markup`
takes the marks back off before TTS, so the spoken audio is just the words.
"""
from __future__ import annotations

import random

from . import vocab

# `ePlaybackMood` values (behavior-markup.md, recovered from Assembly-CSharp).
MOOD_NEUTRAL, MOOD_HAPPY, MOOD_CURIOUS = (
    vocab.MOODS["neutral"], vocab.MOODS["happy"], vocab.MOODS["curious"])


def _thinking_markup(text: str, mood: int, behaviour: str, event_name: str,
                     category: str = "BehaviourTree") -> str:
    """`<playback-mood/><behaviour-tree/> text` — the performed form of one filler.

    Deliberately **not** run through the markup floor (`moxie_sdk.automarkup`), which is
    what generates markup for every line nobody wrote by hand. These eight lines *are*
    written by hand: the mood/tree/gesture triple below is what makes a filler read as
    *thinking* rather than as an answer, and `sim/tests/test_brain_latency.py` pins the
    spoken line as one contiguous run inside its markup — a floor pass would thread a
    `<break>` through it. Marks are still minted in the one place everything else uses
    (`moxie_sdk.vocab`), so a filler is validated against the same frozen catalog; see
    docs/architecture/backlog/expressiveness.md §1.2 ("filler.py — unchanged").
    """
    return (vocab.mood_mark(mood, 1)
            + vocab.tree_mark(event_name, behaviour, category=category, track=None)
            + " " + text)


# (text, mood, behaviour tree, gesture, tree category). Short, kid-appropriate, and
# honest — Moxie says it is thinking, it does not pretend to have answered.
_LINES = (
    ("Hmm, let me think about that one.",
     MOOD_CURIOUS, "Bht_Active_Thinking", "Gesture_Think", "BehaviourTree"),
    ("Ooh, good question! Give me a second.",
     MOOD_CURIOUS, "Bht_Idle_Curious", "Gesture_Question", "BehaviourTree"),
    ("One moment — my thinking gears are spinning.",
     MOOD_NEUTRAL, "Bht_Active_Thinking", "Gesture_Think", "BehaviourTree"),
    ("Hold on, I'm still working that out.",
     MOOD_NEUTRAL, "Bht_Vg_hmm_thinking", "Gesture_Think_Subtle", "Bht_Vocal_Gestures"),
    ("That's a big one. I'm thinking hard!",
     MOOD_HAPPY, "Bht_Active_Thinking", "Gesture_Think", "BehaviourTree"),
    ("Just a sec — I want to get this right.",
     MOOD_CURIOUS, "Bht_Idle_Curious", "Gesture_Think_Subtle", "BehaviourTree"),
    ("Hmmmm. Almost got it.",
     MOOD_CURIOUS, "Bht_Vg_hmm_thinking", "Gesture_Think", "Bht_Vocal_Gestures"),
    ("Thinking, thinking… nearly there.",
     MOOD_NEUTRAL, "Bht_Active_Thinking", "Gesture_Think_Subtle", "BehaviourTree"),
)

#: `((text, markup), …)` — every filler the runtime may speak.
FILLERS = tuple((text, _thinking_markup(text, mood, tree, gesture, category))
                for (text, mood, tree, gesture, category) in _LINES)


def pick_filler(last: str = "", *, rng=None) -> tuple[str, str]:
    """One `(text, markup)` filler, never the one whose text is `last`.

    "Never twice in a row" is the whole point: a child hears these while waiting, and a
    stuck line reads as a broken robot rather than a thinking one (Fork A excludes its
    previous fact the same way). `rng` is injectable so tests are deterministic.
    """
    rng = rng or random
    choices = [f for f in FILLERS if f[0] != last] or list(FILLERS)
    return rng.choice(choices)
