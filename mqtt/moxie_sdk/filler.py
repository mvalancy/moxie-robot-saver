"""
Filler lines — what Moxie says while a slow brain is still thinking.

The robot re-prompts after ~20 s of cloud silence, so a slow brain's turn opens with one
of these as a REPLY_PENDING chunk 0 and the real answer follows
(remote-chat-protocol.md:63). Pattern credit: OpenMoxie Fork A's rotating interludes
(MIT); the lines and markup are ours. Each line carries hand-written behavior markup (a
mood plus a thinking behaviour tree, behavior-markup.md); TTS strips it.
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

    Not run through the markup floor: these lines are hand-staged, and a floor pass would
    thread a `<break>` through the spoken run. Marks still come from `vocab`.
    """
    return (vocab.mood_mark(mood, 1)
            + vocab.tree_mark(event_name, behaviour, category=category, track=None)
            + " " + text)


# (text, mood, behaviour tree, gesture, tree category) — honest: thinking, not answering.
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

    A repeated line reads as a broken robot. `rng` is injectable for tests.
    """
    rng = rng or random
    choices = [f for f in FILLERS if f[0] != last] or list(FILLERS)
    return rng.choice(choices)
