#!/usr/bin/env python3
"""🎭 Regenerate the behavior planner's 22 dialog-act goldens.

One line per `RemoteDialog.DialogAct` (remote-chat-protocol.md:119-122), staged through
`plan` -> `validate` and written out as **JSON `Performance` objects** plus the markup
they render to. JSON rather than markup is the point: a diff in review shows that an
apology stopped being Sad, not that a 240-character mark grew a field.

    python3 sim/tools/build_performance_goldens.py     # from the repo root

Regenerate ONLY when the planner's rules change **on purpose**, and read the diff — every
byte in that file is something a child sees the robot do. `sim/tests/test_performance.py`
pins the result byte for byte in both representations.

`timeout` is reached through `ctx` rather than through words, and says so in its `why`:
it is a property of a turn that never arrived, and no rule over text can see one.
"""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mqtt"))

from moxie_sdk import performance as perf          # noqa: E402
from moxie_sdk import vocab                        # noqa: E402

OUT = os.path.join(ROOT, "sim", "tests", "goldens", "performance.json")

#: (act, line, ctx, why). One representative line per act, in the order the recovered
#: `DialogAct` enum lists them, so the file reads like the taxonomy it implements.
CASES = [
    ("abandon", "Never mind.", {},
     "an abandoned line goes quiet: Shy, no arm gesture, and the eyes go looking "
     "(Bht_Search) rather than holding the child."),
    ("apology", "I am sorry that happened.", {},
     "mood 2 Sad is what shipped content uses for 'I'm sorry...' (8 occurrences, "
     "behavior-markup.md:119). Gesture_Self, and the least-searching tree we have: "
     "there is NO id that lowers the gaze, so the wish is recorded, not invented."),
    ("apology_response", "That is okay, it happens to everyone.", {},
     "reassurance points at the child and holds the gaze; signal "
     "confirmation_agreement."),
    ("appreciation", "You did it! I am so proud of you.", {},
     "praise aimed at the child: Happy at the act's own intensity 2, and the one "
     "gesture in our catalog that is unambiguously a celebration."),
    ("backchannelling", "Mm-hm.", {},
     "the act defined by NOT moving: no arm gesture at all, an attentive tree, and "
     "nothing else. A nod has no id in our catalog, so we do not pretend to one."),
    ("closing", "Goodbye for now, sleep well.", {},
     "the whole-body Bht_Sign_off, which is the app's own goodbye tree; signal "
     "`closing`, the RemoteSignals value that exists for exactly this."),
    ("command", "Tell me more about your day.", {},
     "an imperative points; the gaze holds so the instruction lands on a face."),
    ("comment", "That is really interesting.", {},
     "a reaction, not a stance: Surprised, no signature gesture, a curious look."),
    ("complaint", "That is not fair at all.", {},
     "mood 7 Concerned and a lowered arm; signal complaint_clarification is the "
     "contract's own way to say 'this needs sorting out'."),
    ("factual_question", "What do you want to play today?", {},
     "a question tilts and HOLDS the gaze (Bht_Idle_Near_Focused) — the difference a "
     "child reads as 'it is waiting for me'. `?` also earns the question delivery."),
    ("hold", "Hold on, let me think about that.", {},
     "Bht_Active_Thinking for the whole body, and no arm gesture stacked on top of it; "
     "the thinking cue makes the face Curious."),
    ("neg_answer", "No.", {},
     "a plain no: neutral face, a small lowering gesture, signal "
     "rejection_disagreement."),
    ("opening", "Hello there! It is so good to see you.", {},
     "Bht_Gesture_Greet, the app's own wave. The tree displaces both the arm gesture "
     "and the gaze — one whole-body tree per line."),
    ("opinion", "I think the red one is the best.", {},
     "a stance points at the speaker (Gesture_Self, from the floor's self-word class) "
     "and looks away in thought."),
    ("opinion_question", "What do you think about the blue one?", {},
     "asking for a stance rather than a fact: the same tilt, but a curious look "
     "instead of a held one."),
    ("other", "...", {},
     "nothing to classify and nothing to perform: `other` is the honest answer, and "
     "the line still comes back to rest."),
    ("other_answers", "Maybe, I am not sure yet.", {},
     "hedging is Curious with a thinking gesture — the uncertainty is the content."),
    ("pos_answer", "Yes!", {},
     "an affirmative needs no arm at all; the face and the held gaze carry it."),
    ("statement_non_opinion", "The sky is blue today.", {},
     "the default: the act adds nothing, and the floor's word rules do the work. This "
     "is the row that proves the planner did not throw the floor away."),
    ("thanking", "Thank you for showing me your drawing.", {},
     "gratitude points at the child and holds the gaze; signal "
     "confirmation_agreement."),
    ("timeout", "Are you still there?", {"timed_out": True},
     "REACHED THROUGH ctx, not through words: a turn that never arrived is a property "
     "of the turn, not of the text. The eyes go searching."),
    ("yes_no_question", "Do you want to hear a story?", {},
     "a closed question: the same tilt and held gaze as a factual one, classified by "
     "its auxiliary-verb opener."),
]


def main():
    assert {a for a, *_ in CASES} == set(vocab.DIALOG_ACTS), "a dialog act is missing"
    cases = []
    for act, line, ctx, why in CASES:
        p = perf.validate(perf.plan(line, ctx=ctx))
        assert p is not None, line
        assert p.dialog_act == act, f"{line!r} classified as {p.dialog_act}, not {act}"
        assert not p.dropped, (line, p.dropped)
        assert not vocab.validate_markup(perf.render(p)), line
        cases.append({"act": act, "line": line, "ctx": ctx, "why": why,
                      "performance": perf.to_json(p), "markup": perf.render(p)})
    payload = {
        "_readme": (
            "Byte-exact goldens for mqtt/moxie_sdk/performance.py — one line per "
            "RemoteDialog.DialogAct (22), from docs/architecture/backlog/"
            "expressiveness.md §2.5. The `performance` object is the primary golden "
            "(the planner emits a STRUCTURE, not a string); `markup` is what "
            "`render()` makes of it, pinned so the two halves cannot drift. "
            "Regenerate with sim/tools/build_performance_goldens.py ONLY when the "
            "rules change on purpose, and read the diff: every byte here is something "
            "a child sees the robot do."),
        "cases": cases,
    }
    with open(OUT, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    print(f"wrote {len(cases)} dialog-act goldens -> {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()
