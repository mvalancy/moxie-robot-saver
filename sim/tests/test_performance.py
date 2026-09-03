"""
The behavior planner — `Performance` + `plan`/`validate`/`render`, and the seam it sits on.

What this file is asserting, and why each assertion exists
----------------------------------------------------------
The planner's promise (`docs/architecture/backlog/expressiveness.md` §2) is not "nicer
markup". It is four properties, and every one of them is load-bearing:

* **It does not emit strings.** `plan()` returns a structure; `render()` is the only
  function in the tree that mints a mark. So the goldens here are readable JSON
  `Performance` objects (`goldens/performance.json`) — one line per dialog act, all 22 —
  and a rendering change cannot silently rewrite what a line MEANS.
* **A brain may suggest, it may never authorize.** Every id, wherever it came from, goes
  through `validate()` against the frozen catalog in `vocab.py`. The property test below
  throws mutated performances at it and asserts nothing outside the catalog survives.
* **It always degrades to the floor.** The fault-injection tests break `plan`, `validate`
  and `render` in turn and require the seam to answer with the floor's markup anyway. A
  planner failure costs expressiveness; it may never cost a turn.
* **It never adds a model call, and never adds latency.** It is pure, stdlib, deterministic
  across processes and hash seeds, and measured against the floor rather than against a
  round number.

Hermetic: no creds, no network, no model. Runs in the fast CI tier.
"""
from __future__ import annotations

import glob
import json
import math
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
MQTT_DIR = os.path.join(REPO, "mqtt")
SUPERVISOR_DIR = os.path.join(MQTT_DIR, "supervisor")
for _p in (MQTT_DIR, SUPERVISOR_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from moxie_sdk import performance as perf          # noqa: E402
from moxie_sdk import vocab                        # noqa: E402
from moxie_sdk.tts import strip_markup             # noqa: E402
from moxie_sdk.filler import FILLERS               # noqa: E402

GOLDENS = os.path.join(HERE, "goldens", "performance.json")
BRIDGE_JS = os.path.join(REPO, "sim", "web", "bridge.js")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test runs with the planner on and both counters at zero."""
    monkeypatch.setenv("MOXIE_AUTOMARKUP", "1")
    monkeypatch.setenv("MOXIE_EXPRESSIVE", "planner")
    perf.reset_dropped()
    yield
    perf.reset_dropped()


def _seam():
    """The seam module, imported fresh enough to see the current environment."""
    import markup
    markup.reset_budget()
    return markup


def staged(text, **ctx):
    """`validate(plan(text))` — what the seam actually renders."""
    return perf.validate(perf.plan(text, ctx=ctx))


# =====================================================================================
# (a) The 22 dialog-act goldens — the acceptance criterion, as readable JSON
# =====================================================================================
def _goldens() -> dict:
    with open(GOLDENS) as fh:
        return json.load(fh)


def test_goldens_cover_every_dialog_act():
    """All 22 `RemoteDialog.DialogAct`s, exactly once each. A taxonomy with a hole in it
    is a taxonomy whose gaps nobody notices until a line lands in one."""
    acts = [c["act"] for c in _goldens()["cases"]]
    assert len(acts) == len(set(acts)) == 22, acts
    assert set(acts) == set(vocab.DIALOG_ACTS), \
        sorted(set(vocab.DIALOG_ACTS) ^ set(acts))


@pytest.mark.parametrize("case", _goldens()["cases"], ids=lambda c: c["act"])
def test_golden_performance_is_byte_exact(case):
    """The staged `Performance` for one line per act, pinned as JSON.

    JSON rather than markup on purpose: a diff in review shows that an apology stopped
    being Sad, not that a 240-character mark grew a field."""
    p = staged(case["line"], **(case.get("ctx") or {}))
    assert p is not None, case["line"]
    assert perf.to_json(p) == case["performance"], (
        f"{case['act']}: staged performance changed\n"
        f"  got:  {json.dumps(perf.to_json(p), sort_keys=True)}\n"
        f"  want: {json.dumps(case['performance'], sort_keys=True)}")


@pytest.mark.parametrize("case", _goldens()["cases"], ids=lambda c: c["act"])
def test_golden_markup_is_byte_exact(case):
    """…and the markup that structure renders to, so the two halves cannot drift."""
    assert perf.render(staged(case["line"], **(case.get("ctx") or {}))) == case["markup"]


def test_goldens_round_trip_through_json():
    """`from_json(to_json(p)) == p` — the goldens file is a faithful representation, not
    a lossy rendering of one. A tool that edits a golden must get the same object back."""
    for case in _goldens()["cases"]:
        p = staged(case["line"], **(case.get("ctx") or {}))
        assert perf.from_json(perf.to_json(p)) == p, case["act"]


def test_acts_are_distinguishable_on_the_wire():
    """22 acts that all perform identically would pass every test above and be worthless.

    The point of scoring the act is that the body differs, so require real spread: many
    distinct moods, and questions/apologies/praise/backchannels that are visibly not each
    other."""
    by_act = {c["act"]: staged(c["line"], **(c.get("ctx") or {}))
              for c in _goldens()["cases"]}
    moods = {p.mood for p in by_act.values()}
    assert len(moods) >= 6, f"only {len(moods)} distinct moods across 22 acts: {moods}"
    signals = {p.signal for p in by_act.values()}
    assert len(signals) >= 6, f"only {len(signals)} distinct signals: {signals}"
    # backchannelling is the act defined by NOT moving the arms.
    assert all(b.gesture is None for b in by_act["backchannelling"].beats)
    assert any(b.gaze for b in by_act["backchannelling"].beats)
    # a question tilts and holds the gaze; praise celebrates; an apology neither.
    assert any(b.gesture == "Gesture_Question" for b in by_act["factual_question"].beats)
    assert any(b.gesture == "Gesture_Celebrate" for b in by_act["appreciation"].beats)
    assert by_act["apology"].mood == vocab.MOODS["sad"]
    assert by_act["apology"].signal == "apology"


# =====================================================================================
# The corpus — every line this appliance can actually say
# =====================================================================================
def _content_lines():
    """Every spoken line in the shipped content modules."""
    lines = []

    def walk(node):
        if isinstance(node, str):
            if node.strip() and "<" not in node and "{" not in node:
                lines.append(node)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key in ("say", "text", "line", "prompt", "entry_line", "lines"):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for path in sorted(glob.glob(os.path.join(MQTT_DIR, "content_modules", "*.json"))):
        with open(path) as fh:
            walk(json.load(fh))
    return lines


def _generated_lines(n=260):
    """A deterministic spread of shapes: lengths, punctuation, clauses, contractions."""
    heads = ["I think", "You know what", "Hmm", "Wow", "Oh no", "Let me see", "Guess what",
             "That is", "We could", "My friend", "Tell me", "Do you know", "Yes", "No",
             "Thank you", "I am sorry", "Never mind", "Good morning", "Goodbye"]
    tails = ["today.", "right now!", "if you want?", "one more time.", "and then some!",
             "— it was great.", "; that is the whole story.", "...", "?", "!"]
    middles = ["the big red ball", "your birthday party", "a story about a dragon",
               "what we did at school", "how the moon looks", "your drawing"]
    out = []
    for i in range(n):
        out.append(" ".join((heads[i % len(heads)], middles[(i // 3) % len(middles)],
                             tails[(i // 7) % len(tails)])))
    return out


def corpus():
    lines = [c["line"] for c in _goldens()["cases"]]
    lines += [text for (text, _markup) in FILLERS]
    lines += _content_lines()
    lines += _generated_lines()
    return [ln for ln in lines if ln and ln.strip()]


CORPUS = corpus()


def test_the_corpus_is_actually_a_corpus():
    """A corpus test that quietly ran over nine lines would prove nothing."""
    assert len(CORPUS) >= 280, len(CORPUS)
    assert len(set(CORPUS)) >= 200, "the corpus is mostly duplicates"


# =====================================================================================
# (b) ZERO unknown ids over the corpus
# =====================================================================================
def test_no_unknown_id_anywhere_in_the_corpus():
    """Every id in every staged line — and in the markup it renders to — is in the frozen
    catalog, and `validate()` dropped nothing.

    This is the criterion the whole positive-list design exists to make checkable. A
    non-zero drop count means the planner is choosing vocabulary we cannot justify from
    our own reverse-engineering."""
    perf.reset_dropped()
    bad = []
    for line in CORPUS:
        p = staged(line, turn_key="corpus", icons=True, sfx=True)
        if p is None:
            continue
        assert not p.dropped, (line, p.dropped)
        found = vocab.validate_markup(perf.render(p))
        if found:
            bad.append((line[:50], found))
    assert not bad, bad
    assert perf.dropped_ids() == 0, perf.dropped_ids()


def test_words_are_never_changed_over_the_corpus():
    """S2, the invariant that makes the planner safe to turn on globally: it may add
    marks and spans; it may not add, drop, reorder or substitute one spoken word."""
    for line in CORPUS:
        p = staged(line, turn_key="corpus")
        if p is None:
            continue
        assert strip_markup(perf.render(p)) == strip_markup(line), line


def test_beats_reconstruct_the_line():
    """The structure itself carries every word — `render` is not allowed to be the only
    place the text survives, because then a golden could not be read."""
    for line in CORPUS[:120]:
        p = staged(line, turn_key="corpus")
        if p is None:
            continue
        assert strip_markup(p.text) == strip_markup(line), line


def test_authored_markup_is_left_alone():
    """S1 idempotence: a line that already carries markup is not ours to restage, and
    running the planner over its own output must not double the marks."""
    p = staged("Hi there! I am Moxie.", turn_key="k")
    rendered = perf.render(p)
    assert perf.plan(rendered) is None
    assert _seam().make_markup(rendered) == rendered


# =====================================================================================
# (b, again) The validator — a brain may suggest, it may never authorize
# =====================================================================================
BAD_IDS = {
    "gesture": ["AUTO_GESTURE_ME", "Gesture_We", "Gesture_Small", "Gesture_Discard",
                "gesture_self", "Gesture_Wave"],
    "tree": ["Bht_Nope", "Bht_Eyeseme_Excited", "Talking_Poses", "bht_search"],
    "gaze": ["Bht_Talking_Poses", "Bht_Sleep_Anim", "left", "down", "Bht_Nope"],
    "icon": ["Party", "school", "Birthday_2", "Null"],
    "sfx": ["sfx_made_up", "moxie_theme", "beep"],
    "spurt": ["laugh5", "chuckle", "HMM THINKING"],
    "usel": ["shouty", "Question", "sad"],
}


def test_an_empty_slot_is_not_a_dropped_id():
    """`None` and `""` both mean "this beat does not do that", and neither is a refusal —
    counting them would make the drop counter useless as an acceptance criterion."""
    perf.reset_dropped()
    out = perf.validate(perf.Performance(beats=(perf.Beat(text="hi", gesture="",
                                                          tree=None, usel=""),)))
    assert out.dropped == () and perf.dropped_ids() == 0


@pytest.mark.parametrize("slot,bad", [(s, b) for s, ids in BAD_IDS.items() for b in ids])
def test_validate_drops_every_non_catalog_id(slot, bad):
    """The positive list, one slot at a time. Several of these are OpenMoxie's own ids —
    real, working ids in *their* engine, and not in our recovered catalog, which is
    exactly the class of mistake this gate exists to catch."""
    p = perf.Performance(beats=(perf.Beat(text="hello", **{slot: bad}),))
    out = perf.validate(p)
    assert getattr(out.beats[0], slot) is None, (slot, bad)
    assert out.dropped, (slot, bad)
    assert f"{slot}=" in out.dropped[0]
    assert not vocab.validate_markup(perf.render(out))


@pytest.mark.parametrize("bad", [11, -1, 99, "happy", 1.5, True])
def test_validate_drops_a_bad_beat_mood(bad):
    """A beat's mood is an `ePlaybackMood` **int** 0-10 and nothing else — not a name, not
    a float, and (because `bool` is an `int` in Python) not `True`."""
    perf.reset_dropped()
    out = perf.validate(perf.Performance(beats=(perf.Beat(text="hi", mood=bad),)))
    assert out.beats[0].mood is None, bad
    assert out.dropped and perf.dropped_ids() == 1
    assert "cmd:playback-mood" not in perf.render(out)


def test_the_drop_counter_actually_counts():
    """The counter is an acceptance criterion ("0 unknown ids over the corpus"), so a
    counter stuck at zero would make that criterion vacuous. Assert it moves."""
    perf.reset_dropped()
    assert perf.dropped_ids() == 0
    perf.validate(perf.Performance(beats=(perf.Beat(text="hi", gesture="Gesture_Nope",
                                                    tree="Bht_Nope"),)))
    assert perf.dropped_ids() == 2
    perf.reset_dropped()
    assert perf.dropped_ids() == 0


@pytest.mark.parametrize("slot,bad", [("dialog_act", "smalltalk"), ("emotion", "curious"),
                                      ("signal", "agreement"), ("mood", 11),
                                      ("mood", -1)])
def test_validate_drops_bad_line_level_ids(slot, bad):
    out = perf.validate(perf.Performance(beats=(perf.Beat(text="hi"),), **{slot: bad}))
    assert getattr(out, slot) is None
    assert out.dropped


def test_validate_drops_rather_than_raises_on_the_hot_path():
    """A bad suggestion costs a gesture, never a turn — unless a caller asks for strict."""
    p = perf.Performance(beats=(perf.Beat(text="hi", gesture="Gesture_Nope"),))
    assert perf.validate(p) is not None
    with pytest.raises(ValueError):
        perf.validate(p, strict=True)


def test_validate_is_a_fixed_point_on_good_input():
    """Validating twice must not change anything or count a second drop."""
    p = staged("What do you want to play today?", turn_key="k")
    perf.reset_dropped()
    assert perf.validate(p) == p
    assert perf.dropped_ids() == 0


def test_validate_clamps_out_of_range_intensity_and_break():
    out = perf.validate(perf.Performance(
        beats=(perf.Beat(text="hi", mood=1, mood_intensity=9, break_after=99.0),)))
    assert out.beats[0].mood_intensity == vocab.MAX_INTENSITY
    assert out.beats[0].break_after is None
    assert len(out.dropped) == 2


def test_a_brain_may_suggest():
    """The other direction — a suggestion that IS in the catalog is honored, or the gate
    would be indistinguishable from ignoring the brain entirely."""
    p = staged("Tell me about your day.", gesture="celebrate", mood="surprised",
               dialog_act="appreciation")
    assert p.mood == vocab.MOODS["surprised"]
    assert p.dialog_act == "appreciation"
    assert any(b.gesture == "Gesture_Celebrate" for b in p.beats)


def test_a_brain_may_not_authorize():
    """…and one that is not in the catalog changes nothing and reaches nothing."""
    p = staged("Tell me about your day.", gesture="AUTO_GESTURE_ME",
               mood="ecstatic", dialog_act="smalltalk")
    assert "AUTO_GESTURE_ME" not in perf.render(p)
    assert p.dialog_act == "command"                 # the rules answered instead
    assert p.mood in vocab.MOOD_IDS
    assert not vocab.validate_markup(perf.render(p))


def test_a_model_chosen_id_takes_the_same_path_as_a_rule_chosen_one():
    """The whole point of C6: there is ONE validator, and a hint does not get to skip it.
    A `Beat` built by hand with a model's id is dropped exactly as a `ctx` hint is."""
    handmade = perf.Performance(beats=(perf.Beat(text="hi", gesture="AUTO_GESTURE_YOU"),))
    assert perf.validate(handmade).beats[0].gesture is None
    from_hint = staged("Hi.", gesture="AUTO_GESTURE_YOU")
    assert all(b.gesture != "AUTO_GESTURE_YOU" for b in from_hint.beats)


# =====================================================================================
# The rendered grammar and the anti-twitch limits
# =====================================================================================
_MARK_DATA = re.compile(r'<mark name="cmd:[a-z0-9-]+,data:(\{.*?\})"\s*/>', re.S)


def test_every_rendered_payload_is_json_and_the_document_is_well_formed():
    for line in CORPUS:
        p = staged(line, turn_key="grammar", icons=True)
        if p is None:
            continue
        out = perf.render(p)
        for body in _MARK_DATA.findall(out):
            json.loads(body.replace("+", '"'))       # raises if it is not JSON
        ET.fromstring("<root>" + out + "</root>")    # raises if the spans are unbalanced


def test_rate_limits_hold_on_a_long_paragraph():
    """Twitchiness is the failure mode a child notices, so the caps are asserted, not
    assumed: at most two mood marks (one face plus one transition), one whole-body tree,
    six arm gestures, and never a `<break>` after the final word."""
    line = ("I looked out of the window and the whole sky had gone orange, and the birds "
            "were flying in a long line over the roof of the school, and I wanted to tell "
            "you about it because it was the best thing I saw all week and I think you "
            "would have liked it too.")
    out = perf.render(staged(line, turn_key="long"))
    assert out.count("cmd:playback-mood") <= perf.MAX_MOOD_MARKS
    assert len(re.findall(r"\+behaviour\+:\+Bht_", out)) <= 1
    gestures = [g for g in re.findall(r"\+eventName\+:\+(Gesture_\w+)\+", out)
                if g != "Gesture_None"]
    assert len(gestures) <= 6, gestures
    assert not out.rstrip().endswith("/>" + "</usel>")
    assert "<break" not in out.split(line.split()[-1])[-1]


def test_the_face_changes_at_most_once_per_line():
    """M18's line: several clauses that each score a DIFFERENT mood. Without the cap the
    face would flip on every comma, which is the twitchiness a child notices — so at most
    one transition survives, and the beats prove the cap acted rather than the line being
    uniform by luck."""
    line = ("I am so sorry about that, but wow, that is amazing, and I am confused, "
            "and oops, I did it again.")
    p = staged(line, turn_key="moods")
    marked = [b.mood for b in p.beats if b.mood is not None]
    assert len(marked) <= perf.MAX_MOOD_MARKS, marked
    assert perf.render(p).count("cmd:playback-mood") <= perf.MAX_MOOD_MARKS
    # …and the clauses really do score differently, or the cap was never exercised.
    scores = {perf._score_mood(" ".join(b.text.split()))[0] for b in p.beats if b.text}
    assert len(scores) >= 3, scores


def test_the_gesture_caps_hold_on_a_many_clause_line():
    """M20's line: more carrying clauses than the caps allow. Six per line and three per
    sentence are the numbers; a line that offers ten must still emit at most six."""
    line = ("I want you, and me, and what is up there, and everything down here, and my "
            "big world, and your little one, and who is high, and how is low.")
    out = perf.render(staged(line, turn_key="caps"))
    gestures = [g for g in re.findall(r"\+eventName\+:\+(Gesture_\w+)\+", out)
                if g != "Gesture_None"]
    assert len(gestures) <= 6, gestures
    assert len(gestures) >= 3, f"the caps were never exercised: {gestures}"


def test_a_whole_body_tree_gets_no_arm_gesture_stacked_on_it():
    """M22: a sentence already playing a `Bht_*` must not also throw an arm — two
    animation systems fighting over the same limbs is the failure mode the SIM renders as
    a twitch. Each line here is ONE sentence with a carrying word in it, so a regression
    that dropped the rule would produce a visible extra gesture rather than nothing."""
    # line -> the SAME words with the tree cue swapped out, which must still gesture.
    controls = {
        "Hello, I am so happy to see you.": "Well, I am so happy to see you.",
        "Hold on, let me think about that.": "Well, you can think about that.",
        "Goodbye my friend, I hope you sleep well.":
            "Well my friend, I hope you rest a lot.",
    }
    for line, control in controls.items():
        p = staged(line, turn_key="tree")
        tree_beats = [b for b in p.beats if b.tree]
        assert len(tree_beats) == 1, line
        out = perf.render(p)
        assert len(re.findall(r"\+behaviour\+:\+Bht_", out)) == 1, line
        arms = [g for g in re.findall(r"\+eventName\+:\+(Gesture_\w+)\+", out)
                if g != "Gesture_None"]
        assert arms == [], f"{line}: tree + {arms}"
        # …and the near-identical line WITHOUT a tree cue DOES gesture, so the assertion
        # above is about the rule and not about the words happening to be gesture-free.
        twin = staged(control, turn_key="tree")
        assert all(b.tree is None for b in twin.beats), control
        assert any(b.gesture for b in twin.beats), control


@pytest.mark.parametrize("slot,bad", [("signal", "agreement"), ("emotion", "curious"),
                                      ("look", "left"), ("icon", "Party"),
                                      ("dialog_act", "smalltalk"), ("mood", "ecstatic")])
def test_an_uncatalogued_hint_falls_through_to_the_rules(slot, bad):
    """M9b: a suggestion nobody can honor must cost **nothing**. Leaving a bad value in
    for `validate` to drop later would take the line's emotion (or gaze, or icon) away
    entirely, which is a worse outcome than ignoring the hint — and it is not what the
    mood and gesture hints do."""
    good = staged("Tell me about your day.", turn_key="hint")
    hinted = staged("Tell me about your day.", turn_key="hint", **{slot: bad})
    assert hinted == good, f"a bad {slot} hint changed the performance"
    assert not hinted.dropped


def test_the_line_always_comes_back_to_rest():
    """Every rendered line ends with a `Gesture_None`: the robot may pause between spoken
    segments, and a body frozen mid-gesture is what that looks like on hardware."""
    for line in CORPUS[:80]:
        p = staged(line, turn_key="rest")
        if p is None:
            continue
        assert perf.render(p).rstrip().endswith(
            vocab.tree_mark("Gesture_None")), line


def test_an_icon_is_always_cleared():
    p = staged("Your birthday is on Friday.", icons=True)
    out = perf.render(p)
    assert '+command+:0' in out and '+command+:2' in out
    assert out.index('+command+:0') < out.index('+command+:2')


# =====================================================================================
# (c) Scored output on 100 % of published turns — single AND streamed
# =====================================================================================
SCORED_KEYS = ("mood", "mood_intensity", "dialog_act", "emotion")


def _outputs(replies):
    return [r.get("output") or {} for r in replies]


def test_every_published_turn_carries_scored_output():
    """The single path. Before this slice `Reply.mood`/`dialog_act` were plumbed end to
    end and no app ever set them, so the wire fields specified by ai-seam.md §② were
    always empty."""
    from helpers_runtime import drive_once
    from moxie_sdk.types import Reply

    class Echo:
        name = "echo"

        def respond(self, turn):
            return Reply(text="That is a wonderful idea! What should we do first?")

    out = drive_once(Echo(), "hi")["output"]
    for key in SCORED_KEYS:
        assert out.get(key) not in (None, ""), (key, out)
    assert out["mood"] in vocab.MOODS
    assert out["dialog_act"] in vocab.DIALOG_ACTS
    assert out["emotion"] in vocab.EMOTION_STATES
    assert out.get("signals") and out["signals"][0] in vocab.SIGNALS


def test_every_streamed_chunk_carries_scored_output():
    """C2/C4 — the gap PR #17 opened. `ReplyChunk` had none of these fields, so a streamed
    answer could not be scored even in principle; now every chunk is."""
    from helpers_runtime import make_runtime, drive_turn
    from moxie_sdk.types import ReplyChunk

    parts = ["I am so happy you asked!", "Let me think about that.",
             "What would you like to try first?", "We can start whenever you want."]

    class Streamer:
        name = "streamer"

        def respond(self, turn):                     # pragma: no cover - not used
            raise AssertionError("the streaming path should have answered")

        def respond_stream(self, turn):
            for i, text in enumerate(parts):
                yield ReplyChunk(text=text, final=(i == len(parts) - 1))

    rt, device_id = make_runtime(Streamer())
    drive_turn(rt, device_id, "tell me something")
    replies = rt.client.chat_replies(device_id)
    assert len(replies) == len(parts), replies
    for r in replies:
        out = r["output"]
        for key in SCORED_KEYS:
            assert out.get(key) not in (None, ""), (key, out)


def test_a_streamed_answer_holds_one_face():
    """§2.5: one mood transition at most across a streamed answer. A face that flips on
    every sentence is the thing the per-chunk rule exists to stop."""
    from helpers_runtime import make_runtime, drive_turn
    from moxie_sdk.types import ReplyChunk

    parts = ["Oh no, I am so sorry.", "That sounds really hard.",
             "Do you want to tell me what happened?", "I am listening."]

    class Streamer:
        name = "streamer"

        def respond(self, turn):                     # pragma: no cover
            raise AssertionError

        def respond_stream(self, turn):
            for i, text in enumerate(parts):
                yield ReplyChunk(text=text, final=(i == len(parts) - 1))

    rt, device_id = make_runtime(Streamer())
    drive_turn(rt, device_id, "hi")
    joined = "".join(r["output"]["markup"] for r in rt.client.chat_replies(device_id))
    assert joined.count("cmd:playback-mood") <= perf.MAX_MOOD_MARKS
    assert joined.count("+eventName+:+Gesture_None+") >= len(parts)


def test_an_apps_own_scoring_wins_over_the_seams():
    """The precedence rule: a brain that knows its line is an apology is not overruled by
    a rule engine — but a brain that says nothing still ships a scored turn."""
    from helpers_runtime import drive_once
    from moxie_sdk.types import Reply

    class Opinionated:
        name = "opinionated"

        def respond(self, turn):
            return Reply(text="The sky is blue today.", mood="surprised",
                         dialog_act="opinion", mood_intensity=2)

    out = drive_once(Opinionated(), "hi")["output"]
    assert out["mood"] == "surprised"
    assert out["dialog_act"] == "opinion"
    assert out["mood_intensity"] == 2


def test_a_declined_plan_does_not_cost_the_app_its_own_scoring(monkeypatch):
    """M28b: when the planner declines (or fails), the seam has nothing to score with —
    and the app's own `Reply.mood`/`dialog_act` are then the ONLY scored output there is.
    Degrading to the floor must not also degrade the wire."""
    from helpers_runtime import drive_once
    from moxie_sdk.types import Reply
    monkeypatch.setattr(perf, "plan", lambda *a, **kw: None)

    class Opinionated:
        name = "opinionated"

        def respond(self, turn):
            return Reply(text="The sky is blue today.", mood="surprised",
                         dialog_act="opinion", emotion="surprise", signal="interest",
                         mood_intensity=2)

    out = drive_once(Opinionated(), "hi")["output"]
    assert out["mood"] == "surprised"
    assert out["dialog_act"] == "opinion"
    assert out["emotion"] == "surprise"
    assert out["signals"] == ["interest"]
    assert out["mood_intensity"] == 2


def test_an_apps_invented_scoring_never_reaches_the_wire():
    """M28's line, and a real hole this check found: an app's own scored fields used to be
    overlaid onto `RemoteChatOutput` *without* passing the catalog, so a brain could have
    authorized `dialog_act: "smalltalk"` simply by setting the field. An app is a brain by
    another name and takes the same positive list."""
    from helpers_runtime import drive_once
    from moxie_sdk.types import Reply

    class Inventive:
        name = "inventive"

        def respond(self, turn):
            return Reply(text="The sky is blue today.", mood="ecstatic",
                         dialog_act="smalltalk", emotion="curious",
                         signal="agreement", mood_intensity=9)

    out = drive_once(Inventive(), "hi")["output"]
    assert out.get("dialog_act") in vocab.DIALOG_ACTS
    assert out["dialog_act"] != "smalltalk"
    assert out.get("mood") in vocab.MOODS
    assert out.get("emotion") in vocab.EMOTION_STATES
    assert all(s in vocab.SIGNALS for s in out.get("signals") or [])
    assert 0 <= out.get("mood_intensity", 0) <= vocab.MAX_INTENSITY


def test_an_apps_authored_markup_is_still_spoken_verbatim():
    """Scoring a line must not rewrite one that came with its own markup."""
    from helpers_runtime import drive_once
    from moxie_sdk.types import Reply

    authored = vocab.mood_mark(3, 2) + "I made this myself."

    class Author:
        name = "author"

        def respond(self, turn):
            return Reply(text="I made this myself.", markup=authored)

    out = drive_once(Author(), "hi")["output"]
    assert out["markup"] == authored
    assert out.get("dialog_act")                    # …and it is scored anyway


# =====================================================================================
# (e) Fault injection — every failure lands on the floor
# =====================================================================================
BOOM_POINTS = ["plan", "validate", "render"]


@pytest.mark.parametrize("where", BOOM_POINTS)
def test_a_planner_failure_falls_back_to_the_floor(monkeypatch, where):
    """§2.6: `plan()` returns a Performance, returns None, or blows up — and in every case
    but the first the seam calls `annotate()` and the wire shape is identical. The child
    never notices which one answered."""
    seam = _seam()
    from moxie_sdk.automarkup import annotate

    def boom(*a, **kw):
        raise RuntimeError("injected planner fault")

    monkeypatch.setattr(perf, where, boom)
    line = "That is amazing! You did it!"
    out = seam.make_markup(line, turn_key="k")
    assert out == annotate(line, turn_key="k"), where
    assert strip_markup(out) == strip_markup(line)
    assert not vocab.validate_markup(out)


def test_a_planner_that_declines_falls_back_to_the_floor(monkeypatch):
    """The quiet failure — `plan()` returning None — is the common one (a line already
    carrying markup, or one past the budget guard), and it must be indistinguishable."""
    seam = _seam()
    from moxie_sdk.automarkup import annotate
    monkeypatch.setattr(perf, "plan", lambda *a, **kw: None)
    assert seam.make_markup("Hello there!") == annotate("Hello there!")


def test_a_failing_planner_still_publishes_a_turn(monkeypatch):
    """The end-to-end version: a broken planner must not cost a child their answer."""
    from helpers_runtime import drive_once
    from moxie_sdk.types import Reply

    def boom(*a, **kw):
        raise RuntimeError("injected planner fault")

    monkeypatch.setattr(perf, "plan", boom)

    class Echo:
        name = "echo"

        def respond(self, turn):
            return Reply(text="I am still here and I can still talk.")

    out = drive_once(Echo(), "hi")["output"]
    assert out["text"] == "I am still here and I can still talk."
    assert "cmd:playback-mood" in out["markup"]      # the floor answered
    assert not vocab.validate_markup(out["markup"])


def test_an_over_budget_planner_latches_to_the_floor(monkeypatch):
    """A planner that is slow once is noise; one that is slow every line must stop taxing
    the hot path. The breaker is what turns "should never happen" into "cannot persist"."""
    seam = _seam()
    from moxie_sdk.automarkup import annotate
    real_plan = perf.plan

    def slow(*a, **kw):
        time.sleep((seam.PLAN_BUDGET_MS + 5) / 1000.0)
        return real_plan(*a, **kw)

    monkeypatch.setattr(perf, "plan", slow)
    for _ in range(seam.PLAN_BUDGET_STRIKES):
        seam.make_markup("Hello there, how are you today?")
    assert seam.planner_latched()
    monkeypatch.setattr(perf, "plan", real_plan)
    assert seam.make_markup("Hello there!") == annotate("Hello there!")
    seam.reset_budget()
    assert not seam.planner_latched()


@pytest.mark.parametrize("hostile", ["", "   ", "\n", "<mark/>", "a>b",
                                    "a" * (perf.MAX_PLAN_CHARS + 1)])
def test_plan_declines_rather_than_raising(hostile):
    """`plan` is total: it answers **None** for anything it will not stage, so the seam's
    fallback is reached by a return value and not only by an exception handler. The
    length guard is the one that matters on the hot path — an unbounded line is unbounded
    work between the first token and the first audio."""
    assert perf.plan(hostile) is None


@pytest.mark.parametrize("odd", ["...", "!!!", "?", "—", "3.14", "ok"])
def test_plan_still_stages_a_short_or_odd_line(odd):
    """…and the other direction: `plan` must not decline everything unusual, or the
    fallback test above would pass on a planner that never plans at all."""
    p = perf.validate(perf.plan(odd))
    assert p is not None and p.beats
    assert strip_markup(perf.render(p)) == strip_markup(odd)


# =====================================================================================
# MOXIE_EXPRESSIVE — the one-variable rollback, in all three positions
# =====================================================================================
def test_expressive_off_is_the_v1_passthrough(monkeypatch):
    monkeypatch.setenv("MOXIE_EXPRESSIVE", "off")
    seam = _seam()
    assert seam.expressive_mode() == "off"
    assert seam.make_markup("Hi there!") == "Hi there!"


def test_expressive_floor_renders_with_the_floor_but_still_scores(monkeypatch):
    """`floor` is a *rendering* rollback: the markup goes back to the word-level
    generator, and the wire keeps its scored fields, because scoring and rendering are
    different jobs and only one of them was ever in doubt."""
    monkeypatch.setenv("MOXIE_EXPRESSIVE", "floor")
    seam = _seam()
    from moxie_sdk.automarkup import annotate
    line = "What do you want to play today?"
    st = seam.perform(line, turn_key="k")
    assert st.markup == annotate(line, turn_key="k")
    assert st.scored["dialog_act"] == "factual_question"


def test_automarkup_zero_still_wins(monkeypatch):
    """The floor's own rollback predates this variable and must keep working."""
    monkeypatch.setenv("MOXIE_AUTOMARKUP", "0")
    monkeypatch.setenv("MOXIE_EXPRESSIVE", "planner")
    seam = _seam()
    assert seam.expressive_mode() == "off"
    assert seam.make_markup("Hi there!") == "Hi there!"


def test_an_unknown_mode_falls_back_to_the_default(monkeypatch):
    """A typo in a rollback lever must not take the appliance's voice away."""
    monkeypatch.setenv("MOXIE_EXPRESSIVE", "planer")
    assert _seam().expressive_mode() == "planner"


# =====================================================================================
# (d)'s prerequisite — every id we emit is one the SIM can actually render
# =====================================================================================
def test_every_emitted_id_is_rendered_by_the_browser_sim():
    """The SIM is the only renderer we can assert against (no hardware has ever played
    our markup), so an id it silently ignores is an id that does nothing anywhere we can
    see. Comment lines are ignored: citing an id in a comment is not rendering it."""
    with open(BRIDGE_JS) as fh:
        src = "\n".join(ln for ln in fh if not ln.strip().startswith("//"))
    emitted_g, emitted_b = set(), set()
    for line in CORPUS:
        p = staged(line, turn_key="sim", icons=True, sfx=True)
        if p is None:
            continue
        out = perf.render(p)
        emitted_g |= set(re.findall(r"\+eventName\+:\+(Gesture_\w+)\+", out))
        emitted_b |= set(re.findall(r"\+behaviour\+:\+(Bht_\w+)\+", out))
    missing = [i for i in sorted(emitted_g | emitted_b) if f'"{i}"' not in src]
    assert not missing, f"the SIM does not animate: {missing}"
    assert len(emitted_g | emitted_b) >= 8, sorted(emitted_g | emitted_b)


# =====================================================================================
# (f) Budget — measured against the floor, not against a round number
# =====================================================================================
def _p95(samples):
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(math.ceil(0.95 * len(ordered)) - 1))]


def test_the_planner_costs_about_what_the_floor_costs():
    """(f) no first-audio latency regression. The seam runs once per spoken chunk on the
    hot path between the first token and the first audio, so the planner is measured
    against the generator it replaces rather than against a number someone liked: it may
    not be more than 4x the floor, and it must still clear the floor's own 1 ms budget."""
    from moxie_sdk.automarkup import annotate
    line = ("I looked out of the window and the sky had gone completely orange, and I "
            "wanted to tell you about it right away because it was so beautiful!")
    seam = _seam()

    def timed(fn, n=400):
        out = []
        for i in range(n):
            t0 = time.perf_counter()
            fn(line, turn_key=f"k{i}")
            out.append((time.perf_counter() - t0) * 1000.0)
        return out

    annotate(line, turn_key="warm")                  # import/compile warm-up
    seam.make_markup(line, turn_key="warm")
    floor = _p95(timed(annotate))
    planner = _p95(timed(seam.make_markup))
    assert planner < 1.0, f"planner p95 {planner:.3f} ms/line"
    assert planner <= max(4.0 * floor, 0.4), \
        f"planner p95 {planner:.3f} ms vs floor {floor:.3f} ms"


def test_the_planner_makes_no_model_call_and_touches_no_io(monkeypatch):
    """Deterministic means deterministic: no clock, no `random`, no socket. A regression
    that reached for any of them would make the goldens flaky instead of failing here."""
    import random
    import socket
    monkeypatch.setattr(random, "random", lambda: pytest.fail("planner used random"))
    monkeypatch.setattr(random, "randint",
                        lambda *a: pytest.fail("planner used random"))
    monkeypatch.setattr(socket, "socket",
                        lambda *a, **kw: pytest.fail("planner opened a socket"))
    perf.render(staged("Tell me a story about a dragon, please!", turn_key="k"))


def test_the_planner_imports_only_the_stdlib_and_the_sdk():
    """No new dependency reaches the appliance through this module."""
    src = open(os.path.join(MQTT_DIR, "moxie_sdk", "performance.py")).read()
    imports = set(re.findall(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", src, re.M))
    allowed = {"__future__", "re", "dataclasses", "typing", "json", "hashlib", "os"}
    assert not (imports - allowed - {".", "moxie_sdk"} -
                {i for i in imports if i.startswith(".")}), sorted(imports)


def test_the_same_line_renders_identically_under_different_hash_seeds(tmp_path):
    """Never `hash()`: it is salted per process, so a salted planner would answer one way
    on one worker and another way on the next. Three subprocesses, three seeds."""
    script = tmp_path / "render_once.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {MQTT_DIR!r})\n"
        "from moxie_sdk import performance as p\n"
        "line = 'I looked out of the window and the sky had gone orange, and I ran.'\n"
        "print(p.render(p.validate(p.plan(line, ctx={'turn_key': 'seeded'}))))\n")
    outs = set()
    for seed in ("0", "1", "31337"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        env.pop("MOXIE_EXPRESSIVE", None)
        outs.add(subprocess.run([sys.executable, str(script)], capture_output=True,
                                text=True, env=env, check=True).stdout)
    assert len(outs) == 1, "the planner is not reproducible across hash seeds"


# =====================================================================================
# (d) The preview hook — rehearsal, through the ordinary contract
# =====================================================================================
def _preview_runtime():
    from helpers_runtime import make_runtime

    class Never:
        name = "never"

        def respond(self, turn):                     # pragma: no cover
            raise AssertionError("preview must never call a brain")

    return make_runtime(Never())


def test_preview_publishes_an_ordinary_remote_chat():
    """`sim-as-a-client.md`'s guarantee: there is no SIM-specific API and no
    SIM-specific message. A preview is byte-shaped like a real turn, so whatever is
    subscribed as that device performs it."""
    from helpers_runtime import assert_spec_response
    rt, device_id = _preview_runtime()
    out = rt.preview(device_id, "That is amazing! You did it!")
    assert out["ok"] and out["published"]
    replies = rt.client.chat_replies(device_id)
    assert len(replies) == 1
    assert_spec_response(replies[0], event_id=out["event_id"])
    assert replies[0]["output"]["text"] == "That is amazing! You did it!"
    assert replies[0]["output"]["markup"] == out["markup"]
    assert replies[0]["output"]["dialog_act"] == "appreciation"


def test_preview_records_nothing():
    """No turn, no history, no memory — that is what makes it a rehearsal."""
    rt, device_id = _preview_runtime()
    rt.preview(device_id, "Hello there, it is good to see you.")
    assert not rt.history.get(device_id)


def test_preview_returns_the_staged_performance():
    """The console shows the structure beside the SIM canvas, with any dropped id flagged
    — otherwise an author is left guessing why a gesture never played."""
    rt, device_id = _preview_runtime()
    out = rt.preview(device_id, "What do you want to play today?")
    p = out["performance"]
    assert p["dialog_act"] == "factual_question"
    assert p["beats"] and p["beats"][0]["text"]
    assert out["dropped"] == []
    assert perf.from_json(p) is not None


def test_preview_refuses_an_unknown_device_and_an_empty_line():
    rt, device_id = _preview_runtime()
    assert rt.preview("d_nope", "hi")["error"].startswith("unknown device_id")
    assert rt.preview(device_id, "   ")["error"] == "empty line"
    assert not rt.client.published


def test_preview_refuses_a_robot_that_is_still_pending(tmp_path):
    """A rehearsal is still speech reaching a robot, so it takes the device allowlist like
    every other cloud→robot command. A pending robot is one nobody has let in yet."""
    from helpers_runtime import make_runtime
    from moxie_sdk.store import JsonStore

    class Never:
        name = "never"

        def respond(self, turn):                     # pragma: no cover
            raise AssertionError

    rt, device_id = make_runtime(Never(), allow_unverified_bots=False,
                                 store=JsonStore(str(tmp_path)))
    out = rt.preview(device_id, "Hello there!")
    assert out["ok"] is False and out["error"] == "robot is pending"
    assert not rt.client.published
    # …and once it is permitted, the same call goes through — or the refusal above would
    # be indistinguishable from a preview that never works.
    rt.set_permit(device_id, permitted=True)
    assert rt.preview(device_id, "Hello there!")["ok"] is True


def test_preview_is_gated_by_the_same_safety_classifier():
    """A rehearsal line is still a line a child could hear. Like telehealth, a BLOCK comes
    back to the author with its reason rather than being replaced by a redirect: there is
    a human at the keyboard, and substituting for them helps nobody."""
    rt, device_id = _preview_runtime()
    if rt.safety is None:
        pytest.skip("safety classifier disabled in this environment")
    out = rt.preview(device_id, "I will show you how to make a weapon to hurt someone.")
    assert out["ok"] is False and out.get("blocked")
    assert not rt.client.published


def test_preview_does_not_speak_unless_asked():
    """A preview must not spend a voice call an author did not ask for."""
    from helpers_runtime import CountingSynth
    rt, device_id = _preview_runtime()
    rt._synth = CountingSynth()
    rt.preview(device_id, "Hello there!")
    assert rt._synth.spoken == []
    rt.preview(device_id, "Hello there!", speak=True)
    assert rt._synth.spoken


def test_preview_renders_at_least_ten_lines_on_the_sim_contract():
    """(d)'s Python half: the ten rehearsal lines the SIM harness plays all publish a
    valid, distinguishable performance. The browser half is
    `sim/test_performance_render.mjs`, which drives the same lines through the real
    `bridge.js` and writes the contact sheet."""
    rt, device_id = _preview_runtime()
    lines = [c["line"] for c in _goldens()["cases"]][:12]
    faces = set()
    for line in lines:
        out = rt.preview(device_id, line)
        assert out["ok"], out
        assert not vocab.validate_markup(out["markup"])
        faces.add(out["performance"].get("mood"))
    assert len(rt.client.chat_replies(device_id)) >= 10
    assert len(faces) >= 5, faces
