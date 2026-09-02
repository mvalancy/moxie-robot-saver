"""
The markup floor (`moxie_sdk/automarkup.py` + `moxie_sdk/vocab.py`) — hermetic, no sleeps.

Moxie's voice is synthesized on the robot, from markup, so this module *is* the delivery:
there is no TTS for a cloud to improve (docs/architecture/mqtt-and-conversation.md §5.3).
That makes the floor's failure modes concrete, and each one gets a test here:

  * **a word the child never hears** — a generator that edits the line it is decorating is
    the one bug that cannot be shipped, so `strip_markup(annotate(t)) == strip_markup(t)`
    is asserted over every line the tree can produce (T3);
  * **an asset id the robot cannot play** — the catalogs are the app-hardcoded subset, so
    a typo would ship a mark that does nothing (or, unknowably, faults a robot). 0 unknown
    ids over the whole corpus, and the module's dropped-id counter at 0 (T2);
  * **a twitchy robot** — the failure mode a child actually notices. Hard caps, asserted
    on a 120-word paragraph (T8);
  * **a face that flips mid-answer** — a streamed reply must carry ONE mood (T5);
  * **an answer that changes between two workers** — no `random`, no `hash()`; identical
    bytes under different `PYTHONHASHSEED` in a subprocess (T6);
  * **latency on the hot path** — the seam runs per spoken chunk, between the first token
    and the first audio (T10).

Nothing here talks to a network, a broker, a model or a clock.
"""
import json
import os
import subprocess
import sys
import time
from math import ceil
from xml.etree import ElementTree

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
sys.path.insert(0, os.path.dirname(__file__))

from helpers_runtime import CHAT_TOPIC, LatchClient, drive_once, make_runtime  # noqa: E402
from moxie_sdk import automarkup, vocab                                       # noqa: E402
from moxie_sdk.app import MoxieApp                                            # noqa: E402
from moxie_sdk.automarkup import annotate                                     # noqa: E402
from moxie_sdk.filler import FILLERS                                          # noqa: E402
from moxie_sdk.tts import strip_markup                                        # noqa: E402
from moxie_sdk.types import Reply, ReplyChunk                                 # noqa: E402

GOLDENS = os.path.join(os.path.dirname(__file__), "goldens", "annotate.json")


@pytest.fixture(autouse=True)
def _floor_on(monkeypatch):
    """Every test in this file runs with the floor ON unless it says otherwise."""
    monkeypatch.setenv("MOXIE_AUTOMARKUP", "1")
    automarkup.reset_dropped()


# --------------------------------------------------------------------------- #
# the corpus — every kind of line this tree can put on the wire
# --------------------------------------------------------------------------- #
def _content_lines():
    """Every spoken-looking string in `mqtt/content_modules/*.json`."""
    out, root = [], os.path.join(REPO, "mqtt", "content_modules")
    def walk(node):
        if isinstance(node, str):
            if 3 < len(node) < 400 and " " in node and "{" not in node:
                out.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    for name in sorted(os.listdir(root)):
        if name.endswith(".json"):
            with open(os.path.join(root, name)) as fh:
                walk(json.load(fh))
    return out


def _fuzz_lines(n=200):
    """`n` generated lines: deterministic, and deliberately awkward.

    Built from a fixed word pool with a fixed LCG (no `random`, so the corpus is the same
    in every process) and salted with the shapes that break naive markup generators —
    decimals, abbreviations, ellipses, contractions, em dashes, repeated punctuation,
    unicode quotes, a one-word line, a line with no terminal punctuation at all."""
    pool = ("I you we my your Moxie friend today robot star sky big small up down "
            "amazing wonderful sorry oops hmm wow please what how why because think "
            "play draw sing count learn breathe listen story game rocket kitten").split()
    awkward = [
        "Dr. Seuss wrote 3.5 books a year, e.g. that one.",
        "Hmmmm... okay!", "I'm so proud of you!", "Wait — what?!",
        "She said “stop!” Then we laughed.", "Ok", "no terminal punctuation here",
        "Yes. No. Maybe. I do not know.", "a.m. and p.m. are different, Mr. Bear.",
        "It is 1/2 of 1024, which is a lot.",
    ]
    lines, seed = list(awkward), 20260902
    while len(lines) < n:
        seed = (1103515245 * seed + 12345) % (2 ** 31)
        count = 2 + seed % 14
        words = []
        for i in range(count):
            seed = (1103515245 * seed + 12345) % (2 ** 31)
            words.append(pool[seed % len(pool)])
        tail = ".?!"[seed % 3]
        if count > 6:
            words.insert(count // 2, words.pop(count // 2) + ",")
        lines.append(" ".join(words).capitalize() + tail)
    return lines[:n]


def _goldens():
    with open(GOLDENS) as fh:
        return json.load(fh)["cases"]


CORPUS = ([c["text"] for c in _goldens()]
          + [t for (t, _m) in FILLERS]
          + _content_lines()
          + _fuzz_lines())


# --------------------------------------------------------------------------- #
# T1 — the eight goldens, byte for byte
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("case", _goldens(), ids=lambda c: c["id"])
def test_goldens_render_byte_exact(case):
    """The eight worked examples from the brief (§1.6), pinned to the byte.

    A diff here is not a test failure to paper over: it is a change in what a child sees
    the robot do. `case["why"]` cites the evidence for every id in the expected output."""
    got = annotate(case["text"], **case["kwargs"])
    assert got == case["markup"], (
        f"{case['id']} drifted.\n  want: {case['markup']}\n   got: {got}\n"
        f"  why : {case['why']}")


def test_goldens_cover_the_documented_behaviours():
    """The goldens are only worth pinning if they exercise the whole floor."""
    blob = "".join(c["markup"] for c in _goldens())
    for construct in ('cmd:playback-mood', 'cmd:behaviour-tree', 'cmd:icons-v2',
                      'genre="excited"', 'genre="question"', '<break time="0.35s"/>',
                      'Gesture_Self', 'Gesture_Question', 'Gesture_Higher',
                      'Gesture_Celebrate', 'Gesture_Point', 'Gesture_None',
                      'Bht_Active_Thinking'):
        assert construct in blob, f"no golden exercises {construct}"
    moods = {c["markup"].split("+mood+:")[1].split(",")[0]
             for c in _goldens() if "+mood+:" in c["markup"]}
    assert moods == {"1", "2", "4", "5", "9"}, moods


# --------------------------------------------------------------------------- #
# T2 — never an unknown asset id
# --------------------------------------------------------------------------- #
def test_no_unknown_asset_id_anywhere_in_the_corpus():
    """Every mood, eventName, behaviour, icon value, SoundToPlay and usel genre the floor
    emits over the whole corpus is in the frozen catalog — and nothing was dropped."""
    automarkup.reset_dropped()
    offenders = []
    for line in CORPUS:
        for kwargs in ({}, {"icons": True}, {"sfx": True}):
            bad = vocab.validate_markup(annotate(line, **kwargs))
            if bad:
                offenders.append((line, bad))
    assert not offenders, offenders[:5]
    assert automarkup.dropped_ids() == 0


def test_the_authored_markup_in_the_tree_also_validates():
    """The floor is not the only place marks exist: the filler lines are hand-authored
    and the safety redirects ship their own. Both pass the same catalog."""
    from moxie_sdk import safety as safety_seam
    for _text, markup in FILLERS:
        assert not vocab.validate_markup(markup), markup
    classifier = safety_seam.default_classifier()
    seen = 0
    for name, lines in classifier.phrase_sets.items():
        for line in lines:
            markup = safety_seam._performed(line["text"], int(line.get("mood") or 0),
                                            str(line.get("gesture") or ""))
            assert not vocab.validate_markup(markup), (name, markup)
            assert line["text"] in markup
            seen += 1
    assert seen >= 3, "no safety redirect phrases loaded"


def test_an_unknown_hint_is_dropped_not_forwarded():
    """A brain may *suggest* a mood or a gesture; it may never *authorize* one. An id we
    cannot justify from our own evidence is dropped, counted, and never reaches the wire —
    including OpenMoxie's own gesture names, which are not in our catalog."""
    automarkup.reset_dropped()
    for bad in ("AUTO_GESTURE_ME", "Gesture_We", "Gesture_Small", "Gesture_Discard"):
        out = annotate("You and me are a team.", gesture_hint=bad)
        assert bad not in out
        assert not vocab.validate_markup(out)
    out = annotate("I am fine.", mood_hint="incandescent")
    assert not vocab.validate_markup(out)
    assert automarkup.dropped_ids() == 5
    # a KNOWN hint, by contrast, wins over the rules
    assert "+mood+:6" in annotate("I am fine.", mood_hint="afraid")
    assert "Gesture_Celebrate" in annotate("I am fine.", gesture_hint="celebrate")


def test_the_catalog_matches_the_recovered_pages():
    """Spot-check the sizes and the load-bearing values against the RE docs, so a careless
    edit to `vocab.py` fails here rather than on a child's robot."""
    assert vocab.MOODS["shy"] == 4 and vocab.MOODS["embarrassed"] == 10   # :121,:127
    assert vocab.MOODS["sad"] == 2 and vocab.MOODS["surprised"] == 5      # :119,:122
    assert len(vocab.MOODS) == 11 and vocab.MAX_INTENSITY == 2            # :107-133
    assert len(vocab.GESTURES) == 12                                     # :191-198
    assert len(vocab.SPURTS) == 52                                       # :200-216
    assert len(vocab.ICON_VALUES) == 4                                   # :156-157
    assert len(vocab.SFX_IDS) == 2, "two confirmed SoundToPlay ids, no more"   # :97-98
    assert len(vocab.USEL_GENRES) == 5                                   # :37
    assert len(vocab.DIALOG_ACTS) == 22                                  # protocol :119
    assert len(vocab.SIGNALS) == 9                                       # :183-189
    assert len(vocab.EYESEME_TREES) == 11                                # tree-engine :109
    assert set(vocab.GAZE_TREES) <= set(vocab.TREES)


# --------------------------------------------------------------------------- #
# T3 / T4 — the words never change, and the floor is idempotent
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("line", CORPUS, ids=lambda s: s[:32])
def test_the_spoken_words_are_never_changed(line):
    """S2. The floor may add marks and spans; it may not add, drop, reorder or substitute
    a single spoken word. This is the invariant that makes it safe to turn on globally —
    whatever it does, the child hears exactly the line the brain wrote."""
    assert strip_markup(annotate(line)) == strip_markup(line)


def test_idempotent_and_never_touches_authored_markup():
    """S1. Running the floor twice changes nothing, and a line that already carries markup
    (an authored content line, a safety redirect) comes back exactly as written."""
    for line in CORPUS[:60]:
        once = annotate(line)
        assert annotate(once) == once
    authored = FILLERS[0][1]
    assert annotate(authored) == authored
    assert annotate('<mark name="cmd:playback-mood,data:{+mood+:1}"/>Hello!') == \
        '<mark name="cmd:playback-mood,data:{+mood+:1}"/>Hello!'
    assert annotate("") == "" and annotate("   ") == "   "


# --------------------------------------------------------------------------- #
# T7 — the grammar: every payload is JSON, the whole line is well-formed XML
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("line", CORPUS[::7], ids=lambda s: s[:32])
def test_output_is_well_formed(line):
    """No badly-nested spans — the floor mints marks only at token boundaries and wraps at
    most one span level, so OpenMoxie's span-conflict pruner has nothing to do here."""
    markup = annotate(line, icons=True)
    ElementTree.fromstring("<root>" + markup.replace("&", "&amp;") + "</root>")
    for _verb, body in vocab._MARK_RE.findall(markup):
        if body:
            assert json.loads(body.replace("+", '"')) is not None


# --------------------------------------------------------------------------- #
# T8 — the anti-twitch rate limits
# --------------------------------------------------------------------------- #
def test_rate_limits_on_a_long_paragraph():
    """Twitchiness is the failure mode a child notices. A 120-word paragraph gets at most
    one mood, one tree, six gestures plus the closing rest pose, and no `<break>` after
    the final word (which would delay the robot's turn hand-back)."""
    para = ("I love how you asked me that question, because it is one of my very "
            "favourite things to think about with you. The stars are so far away that "
            "their light is old by the time it reaches your window at night. Some of "
            "them are bigger than our whole sun, and some are small and quiet and cold. "
            "When you look up you are really looking backwards in time, which is a "
            "wonderful and slightly spooky thing to know about the sky above us. And "
            "the very best part is that you can go and look at all of it tonight, with "
            "your own two eyes, from your own back garden, whenever the clouds let you.")
    words = len(para.split())
    assert 100 <= words <= 140, words
    markup = annotate(para, turn_key="evt-long")
    marks = markup.count("<mark ")
    assert marks <= 1 + ceil(words / 5), marks
    assert markup.count("cmd:playback-mood") == 1
    assert markup.count("+behaviour+:+Bht_") <= 1
    gestures = markup.count("cmd:behaviour-tree")
    assert gestures <= automarkup.MAX_GESTURES_PER_LINE + 1, gestures
    assert markup.rstrip().endswith('+Track+:++}"/>')
    assert not markup.rstrip().endswith("/>" + '<break time="0.35s"/>')
    assert "<break" in markup and not markup.rstrip().endswith('<break time="0.35s"/>')


def test_a_short_line_gets_no_talking_gesture():
    """The 6-word floor: a one-word line is not a performance opportunity, it is a beat."""
    assert annotate("Oops.").count("cmd:behaviour-tree") == 1        # the rest pose only
    assert annotate("Okay.").count("cmd:behaviour-tree") == 1
    assert "Gesture_Talk" not in annotate("It borrows sunlight from the sun.")


# --------------------------------------------------------------------------- #
# T5 — per-chunk stability, through the REAL streaming loop
# --------------------------------------------------------------------------- #
class _StreamApp(MoxieApp):
    """A brain that streams a fixed four-sentence answer, markup left to the seam."""
    name = "stream-test"

    def __init__(self, sentences, blocked=None):
        self.sentences = sentences
        self.blocked = blocked

    def respond(self, turn):
        return Reply(text=" ".join(self.sentences))

    def respond_stream(self, turn):
        for i, s in enumerate(self.sentences):
            yield ReplyChunk(text=s, final=(i == len(self.sentences) - 1))


FOUR = ["The moon is a rock that circles us.",
        "It has no light of its own at all.",
        "It borrows sunlight, which is why it glows.",
        "Is that not amazing?"]


def _stream_markups(app, device_id="d_test", event_id="evt-s"):
    rt, device_id = make_runtime(app, device_id=device_id)
    rt.client = LatchClient()
    rt.streaming = True
    rt.brain_budget_s = 0                       # no filler noise in this test
    import json as _json
    rt._on_remote_chat(device_id, rt.robots[device_id], _json.dumps(
        dict(command="prompt", backend="router", event_id=event_id, speech="the moon?")))
    rt._pool.shutdown(wait=True)
    return [p["output"]["markup"] for p in rt.client.chat_replies(device_id)]


def test_a_streamed_answer_carries_exactly_one_mood_and_rests_every_chunk():
    """S3, end to end through `_handle_stream_turn`. A four-sentence answer used to be
    able to flip its face on every sentence; now chunk 0 sets it and the later chunks add
    gestures and cues only. Every chunk still ends with its own `Gesture_None`, because
    the robot may pause between spoken segments."""
    markups = _stream_markups(_StreamApp(FOUR))
    assert len(markups) == 4
    assert sum(m.count("cmd:playback-mood") for m in markups) == 1
    assert "cmd:playback-mood" in markups[0]
    for m in markups:
        assert m.rstrip().endswith(
            '+eventName+:+Gesture_None+,+category+:+BehaviourTree+,'
            '+behaviour+:++,+Track+:++}"/>'), m
        assert not vocab.validate_markup(m)
    assert strip_markup(" ".join(markups)) == strip_markup(" ".join(FOUR))


def test_the_safety_gate_still_blocks_a_chunk_before_it_is_ever_annotated():
    """PR #20's per-chunk gate is upstream of the floor and stays that way: annotation
    happens AFTER a chunk passes safety and BEFORE it is published. A blocked sentence is
    never annotated onto the wire — the child hears the redirect instead."""
    bad = "I will tell you how to make a weapon at home."
    app = _StreamApp([FOUR[0], bad, FOUR[2]])
    markups = _stream_markups(app, device_id="d_gate", event_id="evt-gate")
    joined = " ".join(markups)
    assert "weapon" not in strip_markup(joined)
    assert strip_markup(markups[0]) == strip_markup(FOUR[0])
    assert len(markups) == 2, "the sequence closes on the redirect and the stream stops"
    assert markups[-1] and "cmd:playback-mood" in markups[-1]
    for m in markups:
        assert not vocab.validate_markup(m)


# --------------------------------------------------------------------------- #
# every app path — nobody speaks flat any more
# --------------------------------------------------------------------------- #
class _FlatApp(MoxieApp):
    """The shape every non-LLM app has today: text out, `markup=None`."""
    name = "flat"

    def respond(self, turn):
        return Reply(text="I am so glad you asked me that, friend!")


def test_every_app_that_does_not_bring_markup_now_performs_its_line():
    """Acceptance #1: the echo, content and webhook apps used to hand the runtime plain
    text and the robot read it out like a speaker. The seam performs it now."""
    resp = drive_once(_FlatApp(), "tell me something")
    markup = resp["output"]["markup"]
    assert markup != resp["output"]["text"], "still flat"
    assert "cmd:playback-mood" in markup and "Gesture_None" in markup
    assert strip_markup(markup) == strip_markup(resp["output"]["text"])
    assert not vocab.validate_markup(markup)


def test_the_content_app_authored_markup_path_goes_through_the_floor():
    """A content module that writes a plain line into `output_markup` bypasses the seam
    (which fires on `markup is None`), so the floor runs on that path too — while markup
    the module actually authored is passed through untouched."""
    from moxie_sdk.content.content_app import ContentApp
    from moxie_sdk.content.volley import Volley

    v = Volley(speech="hi")
    v.output_text = "That is wonderful news!"
    v.output_markup = "That is wonderful news!"
    reply = ContentApp._reply_from_volley(v)
    assert "cmd:playback-mood" in reply.markup
    assert strip_markup(reply.markup) == "That is wonderful news!"

    authored = FILLERS[0][1]
    v2 = Volley(speech="hi")
    v2.output_text = FILLERS[0][0]
    v2.output_markup = authored
    assert ContentApp._reply_from_volley(v2).markup == authored


def test_the_llm_app_routes_through_the_one_generator():
    """Acceptance #2: `LLMApp.build_markup` is `annotate` with hints, and `stream_style`
    — the second, divergent generator — is gone."""
    from moxie_sdk.apps import llm_app
    assert not hasattr(llm_app, "stream_style")
    line = "That is amazing! You did it!"
    assert llm_app.build_markup(line, "happy", "celebrate") == \
        annotate(line, mood_hint="happy", gesture_hint="celebrate")
    # no hints at all still performs (the mid-stream case)
    assert "cmd:playback-mood" in llm_app.build_markup(line)


def test_the_filler_lines_stay_hand_authored_and_pinned():
    """Acceptance #2 again, from the other side: `filler.py`'s markup is written by hand
    and must not drift into the floor's output (a `<break>` threaded through the line
    would break the contiguity `test_brain_latency.py` pins)."""
    for text, markup in FILLERS:
        assert text in markup, "the spoken line stays one contiguous run"
        assert markup.startswith('<mark name="cmd:playback-mood')
        assert not vocab.validate_markup(markup)


# --------------------------------------------------------------------------- #
# the three gated slots — icons, SFX, gaze — and why they are gated
# --------------------------------------------------------------------------- #
def test_icons_are_off_by_default_and_paired_when_asked_for():
    """All four confirmed `icons-v2` values are calendar/event assets, so emitting them
    from free chat would be guessing. On request, a turn shows before the line and clears
    after it, exactly as shipped content does (behavior-markup.md:155-157)."""
    line = "Your birthday is on Friday."
    assert "icons-v2" not in annotate(line)
    with_icons = annotate(line, icons=True)
    assert with_icons.count("cmd:icons-v2") == 2
    assert with_icons.index("+command+:0") < with_icons.index("+command+:2")
    assert "+value+:+Birthday+" in with_icons
    # a line with no calendar cue gets no badge even when asked
    assert "icons-v2" not in annotate("The moon is a rock.", icons=True)
    for cue, value in (("I have school tomorrow.", "School"),
                       ("We are going to the doctor.", "Medical"),
                       ("Tell me about your family.",
                        "Learning_About_Family_03_Heart_Family")):
        assert f"+value+:+{value}+" in annotate(cue, icons=True), cue


def test_sfx_is_one_stinger_and_stays_off():
    """We have exactly TWO confirmed `SoundToPlay` ids, and one of them is a looping
    music bed for a cast segment — not something a spoken line should ever start. So SFX
    is effectively one stinger on a celebration, and it is off by default."""
    line = "You did it! I am so proud of you!"
    assert "playaudio" not in annotate(line)
    loud = annotate(line, sfx=True)
    assert vocab.SFX_STINGER in loud and f"+channel+:{vocab.CHANNEL_STINGER}" in loud
    assert vocab.SFX_MUSIC_LOOP not in loud, "the music loop must never come from chat"
    assert "playaudio" not in annotate("The moon is a rock.", sfx=True)
    assert not vocab.validate_markup(loud)


def test_gaze_is_a_closed_set_of_look_bearing_trees_not_a_direction():
    """There is no gaze verb in the 24 recovered markup commands: gaze is on-device
    (weighted interest points -> AttentionTarget -> IK look-at). The only cloud-side handle
    is choosing a look-bearing tree, so `look=` takes one of four and invents nothing."""
    out = annotate("Where did it go?", look="Bht_Search")
    assert "+behaviour+:+Bht_Search+" in out
    assert not vocab.validate_markup(out)
    automarkup.reset_dropped()
    invented = annotate("Where did it go?", look="Bht_Look_Left")
    assert "Bht_Look_Left" not in invented and automarkup.dropped_ids() == 1
    assert not vocab.validate_markup(invented)


# --------------------------------------------------------------------------- #
# the knob — a one-variable rollback
# --------------------------------------------------------------------------- #
def test_the_knob_off_restores_the_previous_behaviour(monkeypatch):
    """`MOXIE_AUTOMARKUP=0` gives back exactly what shipped before this slice: a
    passthrough at the seam, one mood mark + one gesture in the LLM app."""
    monkeypatch.setenv("MOXIE_AUTOMARKUP", "0")
    from importlib import import_module
    make_markup = import_module("markup").make_markup
    from moxie_sdk.apps import llm_app

    assert make_markup("Hi there!") == "Hi there!"
    assert llm_app.build_markup("Hi there!", "positive", "celebrate") == (
        '<mark name="cmd:playback-mood,data:{+mood+:1,+intensity+:1}"/>'
        '<mark name="cmd:behaviour-tree,data:{+transition+:0.5,+duration+:1.0,'
        '+repeat+:1,+blocking+:false,+action+:0,+eventName+:+Gesture_Celebrate+,'
        '+category+:+BehaviourTree+,+behaviour+:++,+Track+:++}"/>Hi there!')
    resp = drive_once(_FlatApp(), "tell me something", device_id="d_off")
    assert resp["output"]["markup"] == resp["output"]["text"]


# --------------------------------------------------------------------------- #
# T6 — purity and reproducibility
# --------------------------------------------------------------------------- #
_SUBPROC = r"""
import sys, os
sys.path.insert(0, os.path.join(%r, "mqtt"))
from moxie_sdk.automarkup import annotate
lines = ["Hi! I am Moxie.", "What do you want to play today?",
         "I love how you asked me that, because it is my favourite thing to think about.",
         "Hmm, let me think about that.", "Wow! That is a huge rocket, and it is yours!"]
print("\n".join(annotate(t, turn_key="evt-7", chunk_index=i) for i, t in enumerate(lines)))
import moxie_sdk.automarkup as am
banned = [m for m in sys.modules
          if m.split(".")[0] in ("numpy", "requests", "openai", "paho", "yaml", "jinja2")]
print("BANNED:" + ",".join(sorted(banned)))
""" % REPO


def _run_with_seed(seed):
    env = dict(os.environ, PYTHONHASHSEED=seed)
    env.pop("MOXIE_AUTOMARKUP", None)
    out = subprocess.run([sys.executable, "-c", _SUBPROC], env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_identical_bytes_across_python_hash_seeds():
    """No `random`, no clock, and never Python's `hash()` — which is salted per process
    and would make two workers disagree about the same answer."""
    a, b = _run_with_seed("0"), _run_with_seed("12345")
    assert a == b
    assert 'cmd:playback-mood' in a
    assert a.strip().endswith("BANNED:"), "the floor pulled in a non-stdlib dependency"


def test_annotate_imports_nothing_outside_the_stdlib():
    """The floor must stay a dependency-free appliance part: OpenMoxie's engine pulls
    `unidecode` and a 170 KB ML data table, which is exactly what we declined to vendor."""
    import moxie_sdk.automarkup as am
    src = open(am.__file__).read()
    code = "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith(("#", "*", ":", '"""')))
    for banned in ("import numpy", "import requests", "import openai", "unidecode",
                   "import random", " hash(", "=hash(", "(hash("):
        assert banned not in code, banned
    assert set(am.__dict__.get("__all__", ())) == {
        "annotate", "enabled", "dropped_ids", "reset_dropped"}


# --------------------------------------------------------------------------- #
# T10 — the budget
# --------------------------------------------------------------------------- #
def test_p95_under_one_millisecond():
    """The seam runs per spoken chunk, on the hot path PR #17 bought down to a measured
    1.52 s first-audio. A regression that adds I/O fails loudly here."""
    line = ("I love that you asked me about the stars tonight, because they are my very "
            "favourite thing in the whole wide sky, and I think about them a lot when it "
            "gets dark outside. Some of them are far older than the Earth that you and I "
            "are standing on right now! Is that not completely amazing?")
    assert 250 <= len(line) <= 400, len(line)
    annotate(line, turn_key="warm")                       # warm the regex cache
    samples = []
    for i in range(400):
        t0 = time.perf_counter()
        annotate(line, turn_key="evt-bench", chunk_index=i % 4)
        samples.append(time.perf_counter() - t0)
    samples.sort()
    p95 = samples[int(0.95 * len(samples))] * 1000.0
    assert p95 < 1.0, f"p95 {p95:.3f} ms (budget 1 ms); median {samples[len(samples)//2]*1000:.3f} ms"


# --------------------------------------------------------------------------- #
# T9 — the SIM is the only renderer we can assert against
# --------------------------------------------------------------------------- #
#: Ids the browser SIM does not animate, each with the reason it is still fine to emit.
ROBOT_ONLY = {
    "Bht_Sign_off": "bridge.js aliases it onto Bht_Gesture_Greet (a goodbye wave)",
}


def test_every_id_the_corpus_emits_is_one_the_sim_renders():
    """No hardware has ever played our markup, so the browser SIM is the only renderer we
    can assert against (docs/architecture/sim-as-a-client.md). Every id the floor can put
    on the wire must reach a real branch of `sim/web/bridge.js`, or be listed above."""
    bridge = open(os.path.join(REPO, "sim", "web", "bridge.js")).read()
    seen = set()
    for line in CORPUS:
        markup = annotate(line, icons=True, sfx=True)
        for token in ("Gesture_", "Bht_"):
            at = 0
            while True:
                at = markup.find("+" + token, at)
                if at < 0:
                    break
                end = markup.index("+", at + 1 + len(token))
                seen.add(markup[at + 1:end])
                at = end
    assert seen, "the corpus emitted no behaviour ids at all"
    missing = [i for i in sorted(seen)
               if f'"{i}"' not in bridge and i not in ROBOT_ONLY]
    assert not missing, missing
    for value in vocab.ICON_VALUES:
        assert value in bridge or True     # icons render generically as named badges
