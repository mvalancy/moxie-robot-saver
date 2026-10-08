"""
The behavior planner — `Performance` + `plan`/`validate`/`render`, and the seam it sits on.

The planner's promise (`backlog/expressiveness.md` §2) is four properties:

* **It does not emit strings.** `plan()` returns a structure and only `render()` mints
  marks, so the goldens are readable `Performance` JSON (`goldens/performance.json`, one
  per dialog act, all 22) and a rendering change cannot rewrite what a line MEANS.
* **A brain may suggest, never authorize.** Every id goes through `validate()` against the
  frozen `vocab.py` catalog; a property test throws mutated performances at it.
* **It always degrades to the floor.** Fault injection breaks `plan`, `validate` and
  `render` in turn; the seam must still answer with the floor's markup.
* **No model call, no added latency.** Pure, stdlib, deterministic across processes and
  hash seeds, and timed against the floor rather than a round number.

Hermetic: no creds, no network, no model.
"""
from __future__ import annotations

import glob
import json
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

from moxie_sdk import performance as perf          # noqa: E402
from moxie_sdk import vocab                        # noqa: E402
from moxie_sdk.tts import strip_markup             # noqa: E402
from moxie_sdk.filler import FILLERS               # noqa: E402
from helpers_web import script_group               # noqa: E402

GOLDENS = os.path.join(HERE, "goldens", "performance.json")


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


_GESTURE = re.compile(r"\+eventName\+:\+(Gesture_\w+)\+")


def arm_gestures(markup):
    """Every arm gesture in rendered markup; `Gesture_None` is the rest pose, not one."""
    return [g for g in _GESTURE.findall(markup) if g != "Gesture_None"]


def says(**reply):
    """A brain that answers every turn with one fixed `Reply(**reply)`."""
    from moxie_sdk.types import Reply

    class Fixed:
        name = "fixed"

        def respond(self, turn):
            return Reply(**reply)
    return Fixed()


def streams(parts):
    """A brain that only streams: `parts` as ReplyChunks, the last one final."""
    from moxie_sdk.types import ReplyChunk

    class Streamer:
        name = "streamer"

        def respond(self, turn):                     # pragma: no cover - not used
            raise AssertionError("the streaming path should have answered")

        def respond_stream(self, turn):
            for i, text in enumerate(parts):
                yield ReplyChunk(text=text, final=(i == len(parts) - 1))
    return Streamer()


class Never:
    """A brain the test asserts is never asked (preview must not call one)."""
    name = "never"

    def respond(self, turn):                         # pragma: no cover
        raise AssertionError("preview must never call a brain")


# (a) The 22 dialog-act goldens — the acceptance criterion, as readable JSON
def _goldens() -> dict:
    with open(GOLDENS) as fh:
        return json.load(fh)


def test_goldens_cover_every_dialog_act():
    """All 22 `RemoteDialog.DialogAct`s, exactly once each — a hole goes unnoticed otherwise."""
    acts = [c["act"] for c in _goldens()["cases"]]
    assert len(acts) == len(set(acts)) == 22, acts
    assert set(acts) == set(vocab.DIALOG_ACTS), \
        sorted(set(vocab.DIALOG_ACTS) ^ set(acts))


@pytest.mark.parametrize("case", _goldens()["cases"], ids=lambda c: c["act"])
def test_golden_performance_is_byte_exact(case):
    """The staged `Performance` per act, pinned as JSON so a diff reads as meaning
    ("the apology stopped being Sad"), not as a 240-character mark growing a field."""
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
    """`from_json(to_json(p)) == p`: the goldens are lossless, so an edited golden round-trips."""
    for case in _goldens()["cases"]:
        p = staged(case["line"], **(case.get("ctx") or {}))
        assert perf.from_json(perf.to_json(p)) == p, case["act"]


def test_acts_are_distinguishable_on_the_wire():
    """22 acts performing identically would pass everything above, so require real spread."""
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


# The corpus — every line this appliance can actually say
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


# (b) ZERO unknown ids over the corpus
def test_no_unknown_id_anywhere_in_the_corpus():
    """Every id in every staged line (and its rendered markup) is in the frozen catalog,
    and `validate()` dropped nothing — a drop means vocabulary we cannot justify."""
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


# (b, again) The validator — a brain may suggest, it may never authorize
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
    """The positive list, one slot at a time. Several are OpenMoxie ids that work in *their*
    engine but are not in our recovered catalog — the mistake this gate exists to catch."""
    p = perf.Performance(beats=(perf.Beat(text="hello", **{slot: bad}),))
    out = perf.validate(p)
    assert getattr(out.beats[0], slot) is None, (slot, bad)
    assert out.dropped, (slot, bad)
    assert f"{slot}=" in out.dropped[0]
    assert not vocab.validate_markup(perf.render(out))


@pytest.mark.parametrize("bad", [11, -1, 99, "happy", 1.5, True])
def test_validate_drops_a_bad_beat_mood(bad):
    """A beat's mood is an `ePlaybackMood` int 0-10 — not a name, a float, or `True`."""
    perf.reset_dropped()
    out = perf.validate(perf.Performance(beats=(perf.Beat(text="hi", mood=bad),)))
    assert out.beats[0].mood is None, bad
    assert out.dropped and perf.dropped_ids() == 1
    assert "cmd:playback-mood" not in perf.render(out)


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
    """A catalogued suggestion is honored, or the gate is just ignoring the brain."""
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
    """C6: ONE validator — a hand-built `Beat` and a `ctx` hint are dropped alike."""
    handmade = perf.Performance(beats=(perf.Beat(text="hi", gesture="AUTO_GESTURE_YOU"),))
    assert perf.validate(handmade).beats[0].gesture is None
    from_hint = staged("Hi.", gesture="AUTO_GESTURE_YOU")
    assert all(b.gesture != "AUTO_GESTURE_YOU" for b in from_hint.beats)


# The rendered grammar and the anti-twitch limits
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
    """Anti-twitch caps: ≤2 mood marks, ≤1 tree, ≤6 arm gestures, no final `<break>`."""
    line = ("I looked out of the window and the whole sky had gone orange, and the birds "
            "were flying in a long line over the roof of the school, and I wanted to tell "
            "you about it because it was the best thing I saw all week and I think you "
            "would have liked it too.")
    out = perf.render(staged(line, turn_key="long"))
    assert out.count("cmd:playback-mood") <= perf.MAX_MOOD_MARKS
    assert len(re.findall(r"\+behaviour\+:\+Bht_", out)) <= 1
    gestures = arm_gestures(out)
    assert len(gestures) <= 6, gestures
    assert not out.rstrip().endswith("/>" + "</usel>")
    assert "<break" not in out.split(line.split()[-1])[-1]


def test_the_face_changes_at_most_once_per_line():
    """M18: clauses that each score a DIFFERENT mood still yield at most one transition,
    and the scores prove the cap acted rather than the line being uniform by luck."""
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
    """M20: a line offering ten carrying clauses still emits at most six gestures."""
    line = ("I want you, and me, and what is up there, and everything down here, and my "
            "big world, and your little one, and who is high, and how is low.")
    out = perf.render(staged(line, turn_key="caps"))
    gestures = arm_gestures(out)
    assert len(gestures) <= 6, gestures
    assert len(gestures) >= 3, f"the caps were never exercised: {gestures}"


def test_a_whole_body_tree_gets_no_arm_gesture_stacked_on_it():
    """M22: a sentence playing a `Bht_*` throws no arm too (two systems fighting over the
    limbs). Each line carries a gesture word, so dropping the rule shows as an extra arm."""
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
        arms = arm_gestures(out)
        assert arms == [], f"{line}: tree + {arms}"
        # The twin WITHOUT a tree cue does gesture, so the rule — not the words — decided.
        twin = staged(control, turn_key="tree")
        assert all(b.tree is None for b in twin.beats), control
        assert any(b.gesture for b in twin.beats), control


@pytest.mark.parametrize("slot,bad", [("signal", "agreement"), ("emotion", "curious"),
                                      ("look", "left"), ("icon", "Party"),
                                      ("dialog_act", "smalltalk"), ("mood", "ecstatic")])
def test_an_uncatalogued_hint_falls_through_to_the_rules(slot, bad):
    """M9b: an unhonorable hint costs nothing — left for `validate` to drop, it would take
    the line's emotion/gaze/icon away entirely instead of letting the rules answer."""
    good = staged("Tell me about your day.", turn_key="hint")
    hinted = staged("Tell me about your day.", turn_key="hint", **{slot: bad})
    assert hinted == good, f"a bad {slot} hint changed the performance"
    assert not hinted.dropped


def test_the_line_always_comes_back_to_rest():
    """Every line ends on `Gesture_None`, or a pause between segments freezes mid-gesture."""
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


# (c) Scored output on 100 % of published turns — single AND streamed
SCORED_KEYS = ("mood", "mood_intensity", "dialog_act", "emotion")


def _outputs(replies):
    return [r.get("output") or {} for r in replies]


def test_every_published_turn_carries_scored_output():
    """The single path: the ai-seam.md §② wire fields are filled even when no app sets them."""
    from helpers_runtime import drive_once
    out = drive_once(says(text="That is a wonderful idea! What should we do first?"),
                     "hi")["output"]
    for key in SCORED_KEYS:
        assert out.get(key) not in (None, ""), (key, out)
    assert out["mood"] in vocab.MOODS
    assert out["dialog_act"] in vocab.DIALOG_ACTS
    assert out["emotion"] in vocab.EMOTION_STATES
    assert out.get("signals") and out["signals"]["single_signal"] in vocab.SIGNALS


def test_every_streamed_chunk_carries_scored_output():
    """C2/C4 — every streamed chunk is scored, not only the single-reply path."""
    from helpers_runtime import make_runtime, drive_turn
    parts = ["I am so happy you asked!", "Let me think about that.",
             "What would you like to try first?", "We can start whenever you want."]
    rt, device_id = make_runtime(streams(parts))
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
    parts = ["Oh no, I am so sorry.", "That sounds really hard.",
             "Do you want to tell me what happened?", "I am listening."]
    rt, device_id = make_runtime(streams(parts))
    drive_turn(rt, device_id, "hi")
    joined = "".join(r["output"]["markup"] for r in rt.client.chat_replies(device_id))
    assert joined.count("cmd:playback-mood") <= perf.MAX_MOOD_MARKS
    assert joined.count("+eventName+:+Gesture_None+") >= len(parts)


def test_no_publish_path_can_forget_to_score(monkeypatch):
    """Every `_publish_chat` carrying words passes `scored=`: no new path ships unscored."""
    from helpers_runtime import runtime_source
    src = runtime_source()
    unscored, seen = [], 0
    for m in re.finditer(r"_publish_chat\(", src):
        if src[max(0, m.start() - 4):m.start()].endswith("def "):
            continue                                   # the definition itself
        i, depth = m.end(), 1
        while depth:                                   # balanced-paren scan of the call
            depth += (src[i] == "(") - (src[i] == ")")
            i += 1
        call, line = src[m.start():i], src[:m.start()].count("\n") + 1
        seen += 1
        # A literal empty `text` is an ACK / modules answer: no words, nothing to score.
        if '"router", ""' in call or 'backend, ""' in call:
            continue
        if "scored=" not in call:
            unscored.append(line)
    assert seen >= 10, f"only found {seen} publish sites — did the scan break?"
    assert not unscored, (
        f"moxie_runtime.py publishes spoken turns without scoring them, at line(s) "
        f"{unscored}. Route the line through `self._stage(...)` and pass `scored=`.")


def test_an_apps_own_scoring_wins_over_the_seams():
    """An app's own (catalogued) scoring is not overruled by the rule engine."""
    from helpers_runtime import drive_once
    out = drive_once(says(text="The sky is blue today.", mood="surprised",
                          dialog_act="opinion", mood_intensity=2), "hi")["output"]
    assert out["mood"] == "surprised"
    assert out["dialog_act"] == "opinion"
    assert out["mood_intensity"] == 2


def test_a_declined_plan_does_not_cost_the_app_its_own_scoring(monkeypatch):
    """M28b: with the planner declined, the app's own scoring is all there is — degrading
    to the floor must not also degrade the wire."""
    from helpers_runtime import drive_once
    monkeypatch.setattr(perf, "plan", lambda *a, **kw: None)
    out = drive_once(says(text="The sky is blue today.", mood="surprised",
                          dialog_act="opinion", emotion="surprise", signal="interest",
                          mood_intensity=2), "hi")["output"]
    assert out["mood"] == "surprised"
    assert out["dialog_act"] == "opinion"
    assert out["emotion"] == "surprise"
    assert out["signals"] == {"single_signal": "interest"}     # a RemoteSignals message
    assert out["mood_intensity"] == 2


def test_an_apps_invented_scoring_never_reaches_the_wire():
    """M28: an app's own scored fields pass the same catalog as a brain's hints — else an
    app could authorize `dialog_act: "smalltalk"` just by setting the field."""
    from helpers_runtime import drive_once
    out = drive_once(says(text="The sky is blue today.", mood="ecstatic",
                          dialog_act="smalltalk", emotion="curious",
                          signal="agreement", mood_intensity=9), "hi")["output"]
    assert out.get("dialog_act") in vocab.DIALOG_ACTS
    assert out["dialog_act"] != "smalltalk"
    assert out.get("mood") in vocab.MOODS
    assert out.get("emotion") in vocab.EMOTION_STATES
    assert all(s in vocab.SIGNALS for s in (out.get("signals") or {}).values())
    assert 0 <= out.get("mood_intensity", 0) <= vocab.MAX_INTENSITY


def test_an_apps_authored_markup_is_still_spoken_verbatim():
    """Scoring a line must not rewrite one that came with its own markup."""
    from helpers_runtime import drive_once
    authored = vocab.mood_mark(3, 2) + "I made this myself."
    out = drive_once(says(text="I made this myself.", markup=authored), "hi")["output"]
    assert out["markup"] == authored
    assert out.get("dialog_act")                    # …and it is scored anyway


# (e) Fault injection — every failure lands on the floor
BOOM_POINTS = ["plan", "validate", "render"]


@pytest.mark.parametrize("where", BOOM_POINTS)
def test_a_planner_failure_falls_back_to_the_floor(monkeypatch, where):
    """§2.6: whichever stage blows up, the seam answers with `annotate()`'s floor markup."""
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
    """The common, quiet failure — `plan()` returning None — is indistinguishable too."""
    seam = _seam()
    from moxie_sdk.automarkup import annotate
    monkeypatch.setattr(perf, "plan", lambda *a, **kw: None)
    assert seam.make_markup("Hello there!") == annotate("Hello there!")


def test_a_failing_planner_still_publishes_a_turn(monkeypatch):
    """The end-to-end version: a broken planner must not cost a child their answer."""
    from helpers_runtime import drive_once

    def boom(*a, **kw):
        raise RuntimeError("injected planner fault")

    monkeypatch.setattr(perf, "plan", boom)
    out = drive_once(says(text="I am still here and I can still talk."), "hi")["output"]
    assert out["text"] == "I am still here and I can still talk."
    assert "cmd:playback-mood" in out["markup"]      # the floor answered
    assert not vocab.validate_markup(out["markup"])


def test_an_over_budget_planner_latches_to_the_floor(monkeypatch):
    """A planner slow on every line trips the breaker and stops taxing the hot path."""
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
    """`plan` is total: None (not an exception) for what it will not stage. The length
    guard bounds hot-path work between the first token and the first audio."""
    assert perf.plan(hostile) is None


@pytest.mark.parametrize("odd", ["...", "!!!", "?", "—", "3.14", "ok"])
def test_plan_still_stages_a_short_or_odd_line(odd):
    """…and it does not decline everything odd, or the test above passes on a no-op planner."""
    p = perf.validate(perf.plan(odd))
    assert p is not None and p.beats
    assert strip_markup(perf.render(p)) == strip_markup(odd)


# MOXIE_EXPRESSIVE — the one-variable rollback, in all three positions
def test_expressive_off_is_the_v1_passthrough(monkeypatch):
    monkeypatch.setenv("MOXIE_EXPRESSIVE", "off")
    seam = _seam()
    assert seam.expressive_mode() == "off"
    assert seam.make_markup("Hi there!") == "Hi there!"


def test_expressive_floor_renders_with_the_floor_but_still_scores(monkeypatch):
    """`floor` rolls back rendering only; the wire keeps its scored fields."""
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


# (d)'s prerequisite — every id we emit is one the SIM can actually render
def test_every_emitted_id_is_rendered_by_the_browser_sim():
    """The SIM is the only renderer we can assert against (no hardware has ever played
    our markup), so an id it silently ignores is an id that does nothing anywhere we can
    see. Comment lines are ignored: citing an id in a comment is not rendering it."""
    src = "\n".join(ln for ln in script_group("bridge").splitlines()
                    if not ln.strip().startswith("//"))
    emitted_g, emitted_b = set(), set()
    for line in CORPUS:
        p = staged(line, turn_key="sim", icons=True, sfx=True)
        if p is None:
            continue
        out = perf.render(p)
        emitted_g |= set(_GESTURE.findall(out))
        emitted_b |= set(re.findall(r"\+behaviour\+:\+(Bht_\w+)\+", out))
    missing = [i for i in sorted(emitted_g | emitted_b) if f'"{i}"' not in src]
    assert not missing, f"the SIM does not animate: {missing}"
    assert len(emitted_g | emitted_b) >= 8, sorted(emitted_g | emitted_b)


# (f) Budget — measured against the floor, not against a round number
def _interleaved_medians(a, b, n=400):
    """Median cost (ms) of `a` and `b`, sampled ALTERNATELY so both absorb the same
    scheduler noise (separate loops drift up to ~38% apart on a loaded box)."""
    xs, ys = [], []
    for i in range(n):
        t0 = time.perf_counter()
        a(i)
        t1 = time.perf_counter()
        b(i)
        t2 = time.perf_counter()
        xs.append((t1 - t0) * 1000.0)
        ys.append((t2 - t1) * 1000.0)
    xs.sort()
    ys.sort()
    return xs[len(xs) // 2], ys[len(ys) // 2]


def test_the_planner_costs_about_what_the_floor_costs():
    """(f) The planner costs ≤4x the floor it replaces, at the MEDIAN (p95 measures the
    scheduler; an absolute ms budget measures the machine). Catches a model call, socket,
    lock or algorithmic regression; both halves regressing together is
    `test_automarkup.py::test_the_floor_costs_about_what_one_pass_over_the_line_costs`."""
    from moxie_sdk.automarkup import annotate
    line = ("I looked out of the window and the sky had gone completely orange, and I "
            "wanted to tell you about it right away because it was so beautiful!")
    seam = _seam()

    annotate(line, turn_key="warm")                  # import/compile warm-up
    seam.make_markup(line, turn_key="warm")
    floor, planner = _interleaved_medians(
        lambda i: annotate(line, turn_key=f"k{i}"),
        lambda i: seam.make_markup(line, turn_key=f"k{i}"),
    )
    assert floor > 0.0, "the floor was too cheap to time"
    assert planner <= 4.0 * floor, (
        f"planner median {planner:.3f} ms is {planner / floor:.2f}x the floor's "
        f"{floor:.3f} ms; budget 4x")


def test_the_planner_makes_no_model_call_and_touches_no_io(monkeypatch):
    """No `random`, socket or file on `perf.render` or `markup.make_markup` — tiny I/O no
    timing budget resolves, so it is trapped directly."""
    import builtins
    import random
    import socket
    seam = _seam()
    text = "Tell me a story about a dragon, please!"
    seam.make_markup(text, turn_key="warm")          # warm every cache BEFORE the traps
    staged(text, turn_key="warm")

    monkeypatch.setattr(random, "random", lambda: pytest.fail("planner used random"))
    monkeypatch.setattr(random, "randint",
                        lambda *a: pytest.fail("planner used random"))
    monkeypatch.setattr(socket, "socket",
                        lambda *a, **kw: pytest.fail("planner opened a socket"))
    monkeypatch.setattr(builtins, "open",
                        lambda *a, **kw: pytest.fail("planner opened a file"))
    monkeypatch.setattr(os, "open", lambda *a, **kw: pytest.fail("planner opened a fd"))

    perf.render(staged(text, turn_key="k"))
    out = seam.make_markup(text, turn_key="k")       # the hot path the robot calls
    assert "<mark" in out, "the seam still has to do its job with the traps installed"


def test_the_same_line_renders_identically_under_different_hash_seeds(tmp_path):
    """Never `hash()` (salted per process): three subprocesses, three seeds, one answer."""
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


# (d) The preview hook — rehearsal, through the ordinary contract
def _preview_runtime():
    from helpers_runtime import make_runtime
    return make_runtime(Never())


def test_preview_publishes_an_ordinary_remote_chat():
    """sim-as-a-client.md: no SIM-specific message — a preview is shaped like a real turn."""
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
    """The console shows the structure and any dropped id, so an author sees why a gesture
    never played."""
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
    """A rehearsal is speech reaching a robot, so it takes the device allowlist."""
    from helpers_runtime import make_runtime
    from moxie_sdk.store import JsonStore
    rt, device_id = make_runtime(Never(), allow_unverified_bots=False,
                                 store=JsonStore(str(tmp_path)))
    out = rt.preview(device_id, "Hello there!")
    assert out["ok"] is False and out["error"] == "robot is pending"
    assert not rt.client.published
    # Once permitted it goes through, or the refusal is indistinguishable from "broken".
    rt.set_permit(device_id, permitted=True)
    assert rt.preview(device_id, "Hello there!")["ok"] is True


def test_preview_is_gated_by_the_same_safety_classifier():
    """A rehearsal line is one a child could hear; a BLOCK returns to the author with its
    reason (a human is at the keyboard) rather than being swapped for a redirect."""
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
    `bridge/`."""
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
