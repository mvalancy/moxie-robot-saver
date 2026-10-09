"""
The markup floor (`moxie_sdk/automarkup.py` + `moxie_sdk/vocab.py`) — hermetic, no sleeps.

Moxie synthesizes her voice on the robot from markup, so this module IS the delivery
(mqtt-and-conversation.md §5.3). Each failure mode gets a test:

  * a word the child never hears — `strip_markup(annotate(t)) == strip_markup(t)` over
    every line the tree can produce (T3);
  * an asset id the robot cannot play — 0 unknown ids over the corpus, dropped counter 0 (T2);
  * a twitchy robot — hard caps on a 120-word paragraph (T8);
  * a face that flips mid-answer — a streamed reply carries ONE mood (T5);
  * two workers disagreeing — no `random`/`hash()`; identical bytes under different
    `PYTHONHASHSEED` (T6);
  * hot-path latency — the seam runs per spoken chunk (T10).

No network, broker, model or clock.
"""
import json
import os
import re
import subprocess
import sys
import time
from math import ceil
from xml.etree import ElementTree

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

from helpers_runtime import LatchClient, drive_once, make_runtime              # noqa: E402
from helpers_web import script_group                                         # noqa: E402
from moxie_sdk import automarkup, vocab                                       # noqa: E402
from moxie_sdk.actions import parse_action_tags, tag_names                    # noqa: E402
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


# the corpus — every kind of line this tree can put on the wire
def _content_lines():
    """Every spoken-looking string in `mqtt/content_modules/*.json`."""
    out, root = [], os.path.join(REPO, "mqtt", "content_modules")
    def walk(node):
        if isinstance(node, str):
            if tag_names(node):      # `<exit>Bye!` is spoken as `Bye!` (moxie_sdk/actions.py)
                node = parse_action_tags(node)[0]
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
    """`n` deterministic, deliberately awkward lines: a fixed LCG over a fixed pool, salted
    with decimals, abbreviations, ellipses, contractions, em dashes, repeated punctuation,
    unicode quotes, a one-word line and a line with no terminal punctuation."""
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


# T1 — the eight goldens, byte for byte
@pytest.mark.parametrize("case", _goldens(), ids=lambda c: c["id"])
def test_goldens_render_byte_exact(case):
    """The brief's eight worked examples (§1.6), to the byte. A diff is a change in what a
    child sees the robot do; `case["why"]` cites the evidence for every id."""
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


# T2 — never an unknown asset id
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
    """Hand-authored safety-redirect marks pass the same catalog (fillers: below)."""
    from moxie_sdk import safety as safety_seam
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
    """A brain may suggest, never authorize: an uncatalogued id (OpenMoxie's gesture names
    included) is dropped, counted, and never reaches the wire."""
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
    """Sizes and load-bearing values vs the RE docs, so a careless `vocab.py` edit fails here."""
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


# T3 / T4 — the words never change, and the floor is idempotent
def test_the_spoken_words_are_never_changed():
    """S2: marks and spans only — never a word added, dropped, reordered or substituted."""
    for line in CORPUS:
        assert strip_markup(annotate(line)) == strip_markup(line), line


def test_idempotent_and_never_touches_authored_markup():
    """S1: twice == once, and already-marked lines (authored, redirects) come back as written."""
    for line in CORPUS[:60]:
        once = annotate(line)
        assert annotate(once) == once
    authored = FILLERS[0][1]
    assert annotate(authored) == authored
    assert annotate('<mark name="cmd:playback-mood,data:{+mood+:1}"/>Hello!') == \
        '<mark name="cmd:playback-mood,data:{+mood+:1}"/>Hello!'
    assert annotate("") == "" and annotate("   ") == "   "


# T7 — the grammar: every payload is JSON, the whole line is well-formed XML
def test_output_is_well_formed():
    """Marks only at token boundaries, at most one span level: no badly-nested spans."""
    for line in CORPUS:
        markup = annotate(line, icons=True)
        ElementTree.fromstring("<root>" + markup.replace("&", "&amp;") + "</root>")
        for _verb, body in vocab._MARK_RE.findall(markup):
            if body:
                assert json.loads(body.replace("+", '"')) is not None, line


# T8 — the anti-twitch rate limits
def test_rate_limits_on_a_long_paragraph():
    """T8: 120 words get ≤1 mood, ≤1 tree, ≤6 gestures + the rest pose, and no final
    `<break>` (it would delay the robot's turn hand-back)."""
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


# T5 — per-chunk stability, through the REAL streaming loop
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
    rt._on_remote_chat(device_id, rt.robots[device_id], json.dumps(
        dict(command="prompt", backend="router", event_id=event_id, speech="the moon?")))
    rt._pool.shutdown(wait=True)
    return [p["output"]["markup"] for p in rt.client.chat_replies(device_id)]


def test_a_streamed_answer_carries_exactly_one_mood_and_rests_every_chunk():
    """S3 through `_handle_stream_turn`: chunk 0 sets the face, later chunks only gesture,
    and every chunk ends on `Gesture_None` (the robot may pause between segments)."""
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
    """The per-chunk safety gate runs before annotation: a blocked sentence never reaches
    the wire — the child hears the redirect instead."""
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


# every app path — nobody speaks flat any more
class _FlatApp(MoxieApp):
    """The shape every non-LLM app has today: text out, `markup=None`."""
    name = "flat"

    def respond(self, turn):
        return Reply(text="I am so glad you asked me that, friend!")


def test_every_app_that_does_not_bring_markup_now_performs_its_line():
    """Acceptance #1: an app returning plain text is performed by the seam."""
    resp = drive_once(_FlatApp(), "tell me something")
    markup = resp["output"]["markup"]
    assert markup != resp["output"]["text"], "still flat"
    assert "cmd:playback-mood" in markup and "Gesture_None" in markup
    assert strip_markup(markup) == strip_markup(resp["output"]["text"])
    assert not vocab.validate_markup(markup)


def test_the_content_app_authored_markup_path_goes_through_the_floor():
    """A plain line in `output_markup` bypasses the seam (`markup is None`), so the floor runs
    there too — while genuinely authored markup passes untouched."""
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
    """Acceptance #2: `LLMApp.build_markup` is `annotate` with hints; no second generator."""
    from moxie_sdk.apps import llm_app
    line = "That is amazing! You did it!"
    assert llm_app.build_markup(line, "happy", "celebrate") == \
        annotate(line, mood_hint="happy", gesture_hint="celebrate")
    # no hints at all still performs (the mid-stream case)
    assert "cmd:playback-mood" in llm_app.build_markup(line)


def test_the_filler_lines_stay_hand_authored_and_pinned():
    """Filler markup stays hand-written: a `<break>` threaded through it would break the
    contiguity `test_brain_latency.py` pins."""
    for text, markup in FILLERS:
        assert text in markup, "the spoken line stays one contiguous run"
        assert markup.startswith('<mark name="cmd:playback-mood')
        assert not vocab.validate_markup(markup)


# the three gated slots — icons, SFX, gaze — and why they are gated
def test_icons_are_off_by_default_and_paired_when_asked_for():
    """Every confirmed `icons-v2` value is a calendar/event asset, so off unless asked; then
    shown before the line and cleared after, as shipped content does (behavior-markup.md:155)."""
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
    """Two confirmed `SoundToPlay` ids, one a looping music bed — so SFX is one celebration
    stinger, off by default, and never the loop."""
    line = "You did it! I am so proud of you!"
    assert "playaudio" not in annotate(line)
    loud = annotate(line, sfx=True)
    assert vocab.SFX_STINGER in loud and f"+channel+:{vocab.CHANNEL_STINGER}" in loud
    assert vocab.SFX_MUSIC_LOOP not in loud, "the music loop must never come from chat"
    assert "playaudio" not in annotate("The moon is a rock.", sfx=True)
    assert not vocab.validate_markup(loud)


def test_gaze_is_a_closed_set_of_look_bearing_trees_not_a_direction():
    """No gaze verb exists (gaze is on-device IK); the only handle is a look-bearing tree,
    so `look=` takes one of four and invents nothing."""
    out = annotate("Where did it go?", look="Bht_Search")
    assert "+behaviour+:+Bht_Search+" in out
    assert not vocab.validate_markup(out)
    automarkup.reset_dropped()
    invented = annotate("Where did it go?", look="Bht_Look_Left")
    assert "Bht_Look_Left" not in invented and automarkup.dropped_ids() == 1
    assert not vocab.validate_markup(invented)


# the knob — a one-variable rollback
def test_the_knob_off_restores_the_previous_behaviour(monkeypatch):
    """`MOXIE_AUTOMARKUP=0`: passthrough at the seam, one mood + one gesture in the LLM app."""
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


# T6 — purity and reproducibility
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
    """No `random`, clock or salted `hash()`: two workers agree byte for byte."""
    a, b = _run_with_seed("0"), _run_with_seed("12345")
    assert a == b
    assert 'cmd:playback-mood' in a
    assert a.strip().endswith("BANNED:"), "the floor pulled in a non-stdlib dependency"


# T10 — the budget, measured against THIS machine rather than against a constant
#: A yardstick of `annotate`'s kind of work, timed interleaved so the ratio cancels load.
_CALIB_WORD = re.compile(r"[A-Za-z']+")


def _calibration_unit(line):
    """~0.35 ms of pure-CPU regex+string work. No I/O, no allocation cliff, no clock."""
    n = 0
    for _ in range(20):
        n += " ".join(w.lower() for w in _CALIB_WORD.findall(line)).count("a")
    return n


def _interleaved_medians(subject, calibrate, n=400):
    """Medians of `subject` and `calibrate`, sampled ALTERNATELY to share scheduler noise."""
    subj, calib = [], []
    for i in range(n):
        t0 = time.perf_counter()
        subject(i)
        t1 = time.perf_counter()
        calibrate(i)
        t2 = time.perf_counter()
        subj.append((t1 - t0) * 1000.0)
        calib.append((t2 - t1) * 1000.0)
    subj.sort()
    calib.sort()
    return subj[len(subj) // 2], calib[len(calib) // 2]


def test_the_floor_costs_about_what_one_pass_over_the_line_costs():
    """T10: the floor is not the expensive part of the per-chunk hot path. A median RATIO
    to an in-run calibration (p95 measures the box): ~0.9 measured, 2.0 budget, an
    injected 0.5 ms sleep reads 6+. Tiny I/O is the next test's job."""
    line = ("I love that you asked me about the stars tonight, because they are my very "
            "favourite thing in the whole wide sky, and I think about them a lot when it "
            "gets dark outside. Some of them are far older than the Earth that you and I "
            "are standing on right now! Is that not completely amazing?")
    assert 250 <= len(line) <= 400, len(line)
    annotate(line, turn_key="warm")                       # warm the regex cache
    _calibration_unit(line)                               # and the yardstick's
    floor_ms, calib_ms = _interleaved_medians(
        lambda i: annotate(line, turn_key="evt-bench", chunk_index=i % 4),
        lambda i: _calibration_unit(line),
    )
    assert calib_ms > 0.0, "the calibration unit was too cheap to time"
    ratio = floor_ms / calib_ms
    assert ratio < 2.0, (
        f"the floor costs {ratio:.2f}x a same-run calibration pass "
        f"(median {floor_ms:.3f} ms vs {calib_ms:.3f} ms); budget 2.0x")


def test_the_hot_path_opens_no_file_and_reaches_no_socket():
    """Added I/O fails loudly, trapped directly (load-immune). `pytest.fail` raises
    `BaseException`, so a fallback that swallows `Exception` cannot hide it."""
    import builtins
    import socket
    line = "Tell me about the stars tonight, because they are my favourite thing!"
    annotate(line, turn_key="warm")                       # warm caches BEFORE the trap

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(builtins, "open", lambda *a, **kw: pytest.fail("the floor opened a file"))
        mp.setattr(os, "open", lambda *a, **kw: pytest.fail("the floor opened a fd"))
        mp.setattr(socket, "socket",
                   lambda *a, **kw: pytest.fail("the floor opened a socket"))
        for i in range(4):
            out = annotate(line, turn_key="evt-io", chunk_index=i)
    assert "<mark" in out and strip_markup(out) == line, \
        "the floor still has to do its job with the trap installed"


# T9 — the SIM is the only renderer we can assert against
#: Ids the browser SIM does not animate, each with the reason it is still fine to emit.
ROBOT_ONLY = {
    "Bht_Sign_off": "bridge/body.js aliases it onto Bht_Gesture_Greet (a goodbye wave)",
}


def test_every_id_the_corpus_emits_is_one_the_sim_renders():
    """The SIM is the only renderer we can assert against (sim-as-a-client.md): every id the
    floor can emit must reach a real branch of `sim/web/bridge/`, or be listed above."""
    bridge = script_group("bridge")
    seen = set()
    for line in CORPUS:
        seen |= set(re.findall(r"\+((?:Gesture_|Bht_)[^+]*)\+",
                               annotate(line, icons=True, sfx=True)))
    assert seen, "the corpus emitted no behaviour ids at all"
    missing = [i for i in sorted(seen)
               if f'"{i}"' not in bridge and i not in ROBOT_ONLY]
    assert not missing, missing
    # Icons need no per-value branch: the SIM renders every ICON_VALUES entry as a named badge.
