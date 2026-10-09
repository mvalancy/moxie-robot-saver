"""The content brain takes its leave, and says hello: a goodbye ends the conversation (so its
memory summary is written), "go to sleep" sleeps, and a module's opener is actually spoken.

`MOXIE_APP=content` is what the compose stack ships. Before this, "bye Moxie" on it got
another follow-up question (both shipped prompts ask one every turn), the conversation never
ended, and the summary waited for a disconnect; an empty `prompt` sent the brain a user
message of `''` and the opener was never heard.

Hermetic: fake brains that count their OWN calls (a `ChatFn` handed to `ContentApp` never
reaches `chat.model_calls()`), tmp storage, the real `MoxieRuntime` over a fake transport.
What goes on the wire (actions, the result code, signals) is compared through `ActionType`
and the wire encoders (`encode_action`, `build_chat_response`), never as wire literals.
"""
import contextlib
import hashlib
import importlib
import itertools
import json
import os
import random
import re
import signal
import threading
import time
import tracemalloc

import pytest

from helpers_ext import CHAT_MODULE, app_with, robot as ext_robot
from helpers_runtime import CHAT_TOPIC, CountingSynth, LatchClient, make_runtime
from moxie_sdk import presence as presence_seam
from moxie_sdk.actions import ACTION_TAG_PROMPT, parse_action_tags, tag_names
from moxie_sdk.apps.llm_app import DEFAULT_PERSONA, LLMApp
from moxie_sdk.content import ContentApp, load_modules
from moxie_sdk.content import ext as E
from moxie_sdk.content import packs as P
from moxie_sdk.memory_items import item_text
from moxie_sdk.memory_store import MemoryStore
from moxie_sdk.store import JsonStore
from moxie_sdk.types import Action, ActionType, ChildProfile, RobotContext, Turn
from moxie_sdk.wire import build_chat_response, encode_action

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MODULES = os.path.join(REPO, "mqtt", "content_modules")
#: Every shipped module, with the conversation a robot runs it under.
SHIPPED = {"starter.json": "FREE_CHAT", "memory_chat.json": "MEMORY_CHAT"}

#: What a brain that ALWAYS asks a follow-up says (what both shipped prompts ask for).
QUESTION = "That sounds fun! What else did you do today?"

#: The two leave-taking rules, in the llm brain's own words (its `ACTION_TAG_PROMPT`).
EXIT_LINE = next(ln for ln in ACTION_TAG_PROMPT.splitlines() if ln.lstrip().startswith("<exit>"))
SLEEP_LINE = next(ln for ln in ACTION_TAG_PROMPT.splitlines()
                  if ln.lstrip().startswith("<sleep>"))


class Brain:
    """A fake brain that never writes a tag. Turn calls (a system message first) get
    `answer`; a summary call (one user message, memory.py) gets `summary`."""

    def __init__(self, answer=QUESTION, summary=None):
        self.answer, self.summary = answer, summary
        self.turns, self.summaries = [], []

    def __call__(self, messages):
        if messages and messages[0].get("role") == "system":
            self.turns.append(messages)
            return self.answer
        self.summaries.append(messages)
        return self.summary or "{}"


def _raw(name):
    with open(os.path.join(MODULES, name), encoding="utf-8") as fh:
        return json.load(fh)


def shipped_app(name, brain, **kw):
    """`ContentApp` as `config.build_content_app()` builds it: the shipped baseline is
    recorded, so the shipped programs get their shipped grants."""
    raw = _raw(name)
    kw.setdefault("memory", False)
    return ContentApp(load_modules(raw), brain, persona=DEFAULT_PERSONA,
                      content_defaults=P.shipped_items(raw), **kw)


def robot(module_id, content_id="default", device_id="d1"):
    return RobotContext(device_id=device_id, child=ChildProfile(nickname="Sam"),
                        module_id=module_id, content_id=content_id)


# --------------------------------------------------------------------------- #
# The Goodbye and Sleep globals, on both shipped modules
# --------------------------------------------------------------------------- #

#: Goodbyes as speech-to-text writes them: punctuation, Moxie/Moxy, a leading ok/um.
GOODBYES = ["bye", "Bye.", "bye bye Moxie", "Bye, Moxie!", "ok bye", "goodbye",
            "Good night, Moxie.", "see you later", "gotta go", "I have to go", "I'm done",
            "stop", "stop Moxie", "Okay, bye bye Moxie!", "um, goodbye", "Bye-bye!",
            "Moxy bye", "I’m done.", "Yeah, I gotta go. Bye!", "please stop",
            "Goodbye Moxie, see you tomorrow!", "night night", "I have to go to bed",
            # the name's mishearings OpenMoxie's own globals allow (MoxieTimers.json:1)
            "Bye, Foxy!", "bye boxy", "Oxy, goodbye!",
            # Whisper's spelling of a lone "bye": alone, after ok or the name, as "good by",
            # or after another bye
            "By.", "Ok, by.", "Moxie, by.", "Good by.", "bye bye by",
            # "I love you" after a farewell
            "Goodbye Moxie, I love you", "bye, I love you", "Bye Moxie, love you so much!",
            "see you later alligator", "See ya later, alligator!"]

#: Ordinary sentences with the same words inside them. A global answers BEFORE the brain,
#: so each of these would be silently hijacked by a pattern one word too loose.
ORDINARY = ["my dog said bye to the mailman", "can you stop the music in the story",
            "last night I had a dream", "I'm done with my drawing, look!",
            "don't stop telling the story", "I said bye to my grandma today",
            "the bus didn't stop at my house", "good night moon is my favorite book",
            "we stayed up all night", "I'm almost done with my homework",
            "are you done yet?", "what does goodbye mean in Spanish",
            "I have to go to the dentist tomorrow", "tell me a goodnight story",
            "my brother never stops talking", "I'm done eating, what should we play?",
            "see you later alligator is a funny song", "we went to the night market",
            "can we play stop and go", "I don't want to stop playing",
            "my mom says I have to go to bed at eight", "why do people say good night",
            "is it time to stop", "I'm not done", "my cat likes to sleep",
            "I don't want to go to bed", "time to sleep is my least favorite time",
            # a bare word can be a plain answer to Moxie ("When do stars come out?")
            "done", "night",
            # near misses of the words a goodbye may carry: "I love you" only after one,
            # "by" only as the last word, and a name alone is not a goodbye
            "I love you Moxie", "I love you", "buy it", "by the way", "stand by me",
            "By then.", "Foxy",
            # "by" after stop, no or yes is a sentence ("stop by my house"), and "I love you"
            # after stop is play: both reach the brain
            "stop by", "please stop by", "no, by", "yes by", "Stop, I love you!"]

SLEEPS = ["go to sleep Moxie", "time to sleep", "Moxie, go to sleep.",
          "you can go to sleep now", "time for bed", "it's bedtime",
          "Good night Moxie, go to sleep.", "Foxy, go to sleep", "go to sleep boxy"]


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_a_goodbye_ends_the_conversation_without_a_model_call(name):
    for speech in GOODBYES:
        brain = Brain()
        reply = shipped_app(name, brain).respond(
            Turn(robot=robot(SHIPPED[name]), speech=speech))
        assert brain.turns == [], f"{name}: {speech!r} cost a model call"
        assert [a.type for a in reply.actions] == [ActionType.EXIT], (speech, reply)
        assert reply.text.strip() and "<" not in reply.text, (speech, reply.text)


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_ordinary_sentences_with_those_words_still_reach_the_brain(name):
    assert len(ORDINARY) >= 20
    for speech in ORDINARY:
        brain = Brain()
        reply = shipped_app(name, brain).respond(
            Turn(robot=robot(SHIPPED[name]), speech=speech))
        assert len(brain.turns) == 1, f"{name}: {speech!r} was answered by a global"
        assert reply.text == QUESTION and reply.actions == [], (speech, reply)


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_go_to_sleep_sleeps_without_a_model_call(name):
    for speech in SLEEPS:
        brain = Brain()
        reply = shipped_app(name, brain).respond(
            Turn(robot=robot(SHIPPED[name]), speech=speech))
        assert brain.turns == [], f"{name}: {speech!r} cost a model call"
        assert [a.type for a in reply.actions] == [ActionType.SLEEP], (speech, reply)
        assert reply.text.strip() and "<" not in reply.text, (speech, reply.text)


def test_goodbyes_vary_and_a_good_night_is_answered_with_one():
    """`random.pick` over several lines, seeded per turn (`ContentApp.run_extension`)."""
    said, night = set(), set()
    for second in range(40):
        app = shipped_app("starter.json", Brain(), clock=lambda s=second: 1_700_000_000 + s)
        said.add(app.respond(Turn(robot=robot("FREE_CHAT"), speech="bye")).text)
        night.add(app.respond(Turn(robot=robot("FREE_CHAT"), speech="good night")).text)
    assert len(said) >= 3, f"one canned goodbye is not a pick: {said}"
    assert all("night" in line.lower() for line in night), night
    assert not said & night


def test_both_shipped_modules_carry_the_same_goodbye_and_sleep():
    """Two copies of one item: a fix to one file must not leave the other behind."""
    starter = {g["name"]: g for g in _raw("starter.json")["globals"]}
    memory = {g["name"]: g for g in _raw("memory_chat.json")["globals"]}
    for item in ("Goodbye", "Sleep"):
        assert starter[item] == memory[item], item


class _Stalled(BaseException):
    """One match ran past its budget (raised from the SIGALRM handler). Not an `Exception`,
    so code under test that catches every `Exception` (`explain`'s evaluator call does)
    cannot swallow it."""


@contextlib.contextmanager
def _hard_limit(seconds):
    """Interrupt the block after `seconds`. `re` checks for signals while it backtracks
    (measured: a catastrophic match is stopped at 0.50 s on Python 3.10 and 3.12), so a
    stalling pattern fails at its budget instead of when its match finally ends. Off the
    main thread, without `setitimer`, or with someone else's alarm armed, it is a no-op
    and the caller's own timing check still applies."""
    if (not hasattr(signal, "setitimer")
            or threading.current_thread() is not threading.main_thread()
            or signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0)):
        yield
        return

    def stalled(signum, frame):
        raise _Stalled()

    previous = signal.signal(signal.SIGALRM, stalled)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def test_a_looping_transcript_cannot_stall_the_patterns():
    """Speech-to-text can loop ("bye bye bye ...", or glued: "byebyebyebye"). Every word of
    these patterns reads one way only. An ambiguous one backtracks exponentially: one match
    of a Goodbye mutated to `(?:bye\\W*)+` doubles per repeat (measured: 0.6-1.3 s at 20
    repeats, 21.7 s once at 25; the absolute times depend on the machine's load), and one
    mutated to `(?:\\w*bye)+` grows about eightfold per glued "byebyebyebye". The count
    ramps up one repeat at a time, which already stops a doubling pattern one repeat past
    the budget, 0.9-1.4 s into the test with the alarm or without it. The hard 0.5 s alarm
    on every match is a bound, not a speedup: no single match runs longer than 0.5 s,
    however steep the pattern's growth."""
    module = load_modules(_raw("starter.json"))
    patterns = [g for g in module.globals if g.name in ("Goodbye", "Sleep")]
    assert len(patterns) == 2
    for unit in ("bye ", "bye moxie ", "ok bye ", "bye-", "byebye ", "byebyebyebye ",
                 "now ", "moxie ", "good night ", "i'm done ", "go to sleep ", "stop ",
                 "by ", "foxy ", "bye i love you ", "love you ", "see you later alligator ",
                 "stop by ", "ok by ", "stop i love you "):
        for n in (*range(1, 26), 100, 400, 2000):
            for tail in ("x", "!"):
                text = unit * n + tail
                for g in patterns:
                    where = f"{g.name} on {unit!r}*{n}+{tail!r}"
                    started = time.perf_counter()
                    try:
                        with _hard_limit(0.5):
                            g.match(text)
                    except _Stalled:
                        pytest.fail(f"{where} was still matching after 0.5 s")
                    took = time.perf_counter() - started
                    assert took < 0.5, f"{where} took {took:.2f}s"


def test_an_edited_goodbye_falls_back_to_the_brain_which_is_taught_the_same_rule():
    """`random` is a shipped grant, anchored to the program's digest: a changed program
    does not run, and the turn goes to the brain, which carries the <exit> rule."""
    raw = _raw("starter.json")
    defaults = P.shipped_items(raw)
    edited = json.loads(json.dumps(defaults["global:Goodbye"]))
    rule = edited["data"]["extension"]["rules"][-1]
    rule["do"][0]["say"]["random.pick"][0]["lit"][0] = "<exit>Later, alligator!"
    brain = Brain()
    app = ContentApp(P.build_module(defaults, {"global:Goodbye": edited}), brain,
                     persona=DEFAULT_PERSONA, content_defaults=defaults, memory=False)
    reply = app.respond(Turn(robot=robot("FREE_CHAT"), speech="bye"))
    assert len(brain.turns) == 1 and reply.text == QUESTION
    assert brain.turns[0][0]["content"].count(EXIT_LINE) == 1


def test_an_edited_goodbye_pattern_keeps_the_shipped_lines():
    """The grant's digest covers the program (`extension`) only, so a parent who changes
    the pattern (what the console's editor can change; it never touches a program) keeps
    the shipped goodbye running."""
    raw = _raw("starter.json")
    defaults = P.shipped_items(raw)
    edited = json.loads(json.dumps(defaults["global:Goodbye"]))
    edited["data"]["pattern"] = r"^\W*(?:toodles|ta\W*ta)\W*$"
    brain = Brain()
    app = ContentApp(P.build_module(defaults, {"global:Goodbye": edited}), brain,
                     persona=DEFAULT_PERSONA, content_defaults=defaults, memory=False)
    reply = app.respond(Turn(robot=robot("FREE_CHAT"), speech="Toodles!"))
    assert brain.turns == [], "the edited pattern's goodbye cost a model call"
    assert [a.type for a in reply.actions] == [ActionType.EXIT] and reply.text, reply


# --------------------------------------------------------------------------- #
# What a parent reads about them (`ext.explain`, shown in the pack review)
# --------------------------------------------------------------------------- #

SHIPPED_EXPLAIN = {
    "Goodbye": [
        "When what your child said contains 'night' or what your child said contains 'bed' "
        "or what your child said contains 'sleep': says one of 3 goodbyes (picked "
        "unpredictably) and answers without asking the AI; then the conversation ends.",
        "Whenever this activity is triggered: says one of 5 goodbyes (picked unpredictably) "
        "and answers without asking the AI; then the conversation ends.",
    ],
    "Sleep": [
        "Whenever this activity is triggered: tells your child 'Okay, sleepy time! Good "
        "night.' and answers without asking the AI; then Moxie goes to sleep.",
    ],
}


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_a_parent_reads_the_shipped_goodbye_and_sleep_plainly(name):
    """It used to read "tells your child one of them, picked unpredictably a fixed list of
    options", and Sleep's line showed its raw `<sleep>` tag."""
    shipped = {g["name"]: g for g in _raw(name)["globals"]}
    for item, want in SHIPPED_EXPLAIN.items():
        lines = E.explain(shipped[item]["extension"])
        assert lines == want, (item, lines)
        for line in lines:                  # T13's rules (test_ext.py) hold here too
            assert "<" not in line and "{" not in line and "[" not in line, line
            for cap in E.CAPABILITY_WORDS:
                assert not re.search(rf"\b{re.escape(cap)}\b", line), (cap, line)


def _effect_of(action):
    """What `ext.explain()` calls an action a robot is sent. A module is said bare when it
    is an id (`DRAW`, `free_chat`, `A-1`) and quoted otherwise, so author text never reads
    as part of the sentence."""
    if action.type == ActionType.EXIT:
        return "the conversation ends"
    if action.type == ActionType.SLEEP:
        return "Moxie goes to sleep"
    assert action.type == ActionType.LAUNCH, action
    module = action.module_id
    return (f"Moxie starts the {module} activity" if re.fullmatch(r"[A-Za-z0-9_-]+", module)
            else f"Moxie starts the '{module}' activity")


def test_explain_reads_a_tag_as_the_robot_does_and_nothing_else_moved():
    """explain.py restates the tag grammar (its package imports nothing outside itself), so
    for every line it must name exactly the actions `actions.parse_action_tags` makes of
    it, in order: the same kinds and the same modules, not only as many. The six
    conformance rows still read exactly as recorded."""
    from moxie_sdk.content.ext.explain import _tag_effects
    for line in ["<exit>Bye!", "Night.<sleep>", "Let's draw!<launch:DRAW>",
                 "<launch:DRAW:default>Go!", "<launch_if_confirmed:DRAW>Draw?", "<EXIT>Bye",
                 "< sleep >zz", "<exit:now>hm", "<launch>no", "<launch:A:B:C>no",
                 "<launch: :x>no", "<opener>Hi", "<mark name='x'/>Hi", "plain",
                 "<exit:>ok", "<sleep : >zz", "<launch:DRAW:>go", "<launch:A:B:>go",
                 "<Launch:Draw>hi", "<launch_if_confirmed:A:B>x", "<sleep><exit>both",
                 "<exit>a<exit>twice", "<launch::x>no", "<exitx>no", "<launch_x:A>no",
                 "<<exit>>x", "<launch:DR<exit>AW>x", "<\u017fleep>no", "<exit\u00a0>nb"]:
        read = _tag_effects(line)
        assert all(sure for _, sure in read), line
        assert [effect for effect, _ in read] == \
            [_effect_of(a) for a in parse_action_tags(line)[1]], line
    with open(os.path.join(REPO, "sim", "tests", "data", "ext_conformance.json"),
              encoding="utf-8") as fh:
        rows = json.load(fh)["rows"]
    assert len(rows) == 6
    for row in rows:
        assert E.explain(row["ast"]) == row["explain"], row["name"]


def _imported(rule, caps=("handled", "say")):
    return {"ext_format": 1, "capabilities": list(caps), "on": "global", "rules": [rule]}


def _says(say, let=None, caps=("handled", "say")):
    rule = {"do": [{"say": say}, {"handled": True}]}
    if let:
        rule["let"] = let
    return _imported(rule, caps)


#: The module itself (the package re-exports `explain`, the function, under that name).
_EXPLAIN = importlib.import_module("moxie_sdk.content.ext.explain")


@contextlib.contextmanager
def _past_the_budget():
    """`explain()` with nothing left to build, as after a program that used it all up: a
    `say` that needs to build anything is read from the program's own text alone."""
    saved, _EXPLAIN._BUDGET = _EXPLAIN._BUDGET, 0
    try:
        yield
    finally:
        _EXPLAIN._BUDGET = saved


def _pick(first, second):
    """`get` at an index worked out from what the child said: `first` when it is two
    characters long ("ab"), `second` when it is three ("abc")."""
    return {"get": [{"lit": [first, second]}, {"%": [{"len": [{"var": "speech"}]}, 2]}]}


DRAW = "Moxie starts the DRAW activity"
WORKED_OUT = "Moxie starts an activity it works out"
_WANTS_DRAW = {"contains": [{"lower": [{"var": "speech"}]}, "draw"]}
#: Nine parts that each come out two ways: a `concat` that holds them can say 512 different
#: lines, past the 256 `explain()` reads one by one.
_NINE_IFS = [{"if": [{"var": "speech"}, "a", "b"]}] * 9
#: Parts worked out at run time that always come out as nothing: a path below a string, a
#: value the robot did not send, and a list (said as nothing).
_ALWAYS_EMPTY = {"var": "speech.x"}
_NOT_SENT = {"var": "input_vars.zz"}
_A_LIST = {"list": [{"var": "speech"}]}

#: Imported programs whose `say` reaches an action tag in different ways: how the pack
#: review's sentence ends, and what the robot is sent for each thing the child says. A line
#: acts only on a tag written whole in its rule's own text (a string literal in a `say` or a
#: `let` value, `ext_host.literal_actions`): the host takes any other tag out of the line
#: before it is kept, so the shapes that build one (from pieces, a case op, a cutting op or
#: run-time text) send nothing, and the sentence names only what is written whole, as
#: "sometimes" where the reading ahead cannot say it happens every time. Before round 2,
#: every one but the plain line and `random.pick` read with no "then" at all; before round 6
#: the built tags acted, and five review rounds each found one the reading ahead missed.
IMPORTED_SAYS = {
    "if with a fixed test": (
        _says({"if": [True, "<launch:DRAW>Let's draw!", "Hi"]}),
        f"; then {DRAW}.", {"hi there": [DRAW]}),
    "if on what the child said": (
        _says({"if": [_WANTS_DRAW, "<launch:DRAW>Let's draw!", "Hi"]}),
        f"; then sometimes {DRAW}.", {"let's draw": [DRAW], "hello": []}),
    "upper": (
        _says({"upper": ["<exit>bye now"]}),
        "; then the conversation ends.", {"hi": ["the conversation ends"]}),
    # The line carries `<launch:draw>`, a module the rule's text does not write (fields
    # compare exactly): taken out. The DRAW written is named, as "sometimes".
    "lower, which lowers the module too": (
        _says({"lower": ["<launch:DRAW>OK"]}),
        f"; then sometimes {DRAW}.", {"hi": []}),
    "concat": (
        _says({"concat": ["<sleep>", "Night ", "night"]}),
        "; then Moxie goes to sleep.", {"hi": ["Moxie goes to sleep"]}),
    "a tag split across concat parts": (
        _says({"concat": ["<ex", "it>See you!"]}), "", {"hi": []}),
    "a let-bound line": (
        _says({"var": "line"}, let={"line": "<exit>See you!"}),
        "; then the conversation ends.", {"hi": ["the conversation ends"]}),
    "a let bound to an earlier let": (
        _says({"var": "nap"}, let={"tag": "<sleep>",
                                   "nap": {"concat": [{"var": "tag"}, "Nap time!"]}}),
        "; then Moxie goes to sleep.", {"hi": ["Moxie goes to sleep"]}),
    "or": (
        _says({"or": [{"var": "input_vars.line"}, "<exit>Bye"]}),
        "; then sometimes the conversation ends.", {"hi": ["the conversation ends"]}),
    "a tag assembled from literals": (
        _says({"replace": ["<exot>Bye", "o", "i"]}), "", {"hi": []}),
    "get at a worked-out index": (
        _says({"get": [{"lit": ["<exit>Bye", "Hi"]},
                       {"%": [{"len": [{"var": "speech"}]}, 2]}]}),
        "; then sometimes the conversation ends.",
        {"ab": ["the conversation ends"], "abc": []}),
    "a tag inside a list that get takes from": (
        _says({"get": [{"var": "said"}, 0]},
              let={"said": {"if": [{"contains": [{"lower": [{"var": "speech"}]}, "bye"]},
                                   {"lit": ["<exit>See you!"]}, {"lit": ["Hi!"]}]}}),
        "; then sometimes the conversation ends.",
        {"bye now": ["the conversation ends"], "hello": []}),
    "a launch of a worked-out module": (
        _says({"concat": ["<launch:", {"upper": [{"trim": [{"var": "speech"}]}]},
                          ">Off we go!"]}),
        "", {"draw": []}),
    "a line that is only a tag": (
        _says("<sleep>"), "; then Moxie goes to sleep.", {"hi": ["Moxie goes to sleep"]}),
    # The first line's exit is written whole in the rule, so it is named (as "sometimes":
    # the last `say` replaces it, `Volley.set_output`, and the robot is sent nothing).
    "two lines, of which the robot is sent the last": (
        _imported({"do": [{"say": "<exit>Bye"}, {"say": "Hi there"}, {"handled": True}]}),
        "; then sometimes the conversation ends.", {"hi": []}),
    # A worked-out part may come out as nothing, and a tag around it then forms: the host
    # takes it out, since no piece writes it whole.
    "an always-empty part inside the tag's name": (
        _says({"concat": ["<ex", _ALWAYS_EMPTY, "it>Bye!"]}), "", {"hi": []}),
    "a value the robot did not send inside the tag's name": (
        _says({"concat": ["<ex", _NOT_SENT, "it>Bye!"]}), "", {"hi": []}),
    "a list inside the tag's name": (
        _says({"concat": ["<ex", _A_LIST, "it>Bye!"]}), "", {"hi": []}),
    "a dotted let path inside the tag's name": (
        _says({"concat": ["<sl", {"var": "x.y"}, "eep>Night"]}, let={"x": "hello"}),
        "", {"hi": []}),
    "an empty part between launch and its module": (
        _says({"concat": ["<launch", _NOT_SENT, ":DRAW>Let's draw!"]}), "", {"hi": []}),
    "a tag's name split around an empty part, with its module worked out": (
        _says({"concat": ["<la", _NOT_SENT, "unch:", {"upper": [{"trim": [{"var": "speech"}]}]},
                          ">Off we go!"]}),
        "", {"draw": [], "hi": []}),
    "a module split around an empty part": (
        _says({"concat": ["<launch:DR", _ALWAYS_EMPTY, "AW>Go!"]}), "", {"hi": []}),
    "an and whose earlier operand is a falsy number": (
        _says({"concat": ["<launch:", {"and": [{"len": [_NOT_SENT]}, "X"]}, ">Go"]}),
        "", {"hi": []}),
    # Past 256 lines, each `concat` is read as one text.
    "a tag split across literal parts, past 256 lines": (
        _says({"concat": _NINE_IFS + ["<ex", "it>Bye"]}), "", {"hi": []}),
    "a lowered launch, past 256 lines": (
        _says({"lower": [{"concat": _NINE_IFS + ["<launch:DRAW>OK"]}]}),
        f"; then sometimes {DRAW}.", {"hi": []}),
    "a nested concat that completes a tag, past 256 lines": (
        _says({"concat": _NINE_IFS + ["<ex", {"concat": ["it>", {"var": "speech"}]}]}),
        "", {"hi": []}),
    "a part built only from literals that completes a tag, past 256 lines": (
        _says({"concat": _NINE_IFS + ["<ex", {"replace": ["iz>Bye", "z", "t"]}]}),
        "", {"hi": []}),
    "a let-bound line that completes a tag, past 256 lines": (
        _says({"concat": _NINE_IFS + ["<sl", {"var": "rest"}]},
              let={"rest": {"concat": ["eep>Night, ", {"var": "speech"}]}}),
        "", {"hi": []}),
    "an upper part that completes a tag, past 256 lines": (
        _says({"concat": _NINE_IFS + ["<EX", {"upper": [{"concat": ["it>", {"var": "speech"}]}]}]}),
        "", {"hi": []}),
    # Round 4: `trim` takes the spaces beside a part that came out as nothing, and a case op
    # over an op `explain()` does not follow changes the tag that op hands on.
    "a trimmed part that starts with an always-empty part, then spaces": (
        _says({"concat": ["<ex", {"trim": [{"concat": [_ALWAYS_EMPTY, "  it>Bye"]}]}]}),
        "", {"hi": []}),
    "lower over a get at a worked-out index": (
        _says({"lower": [_pick("<launch:DRAW>Go", "Hi")]}),
        f"; then sometimes {DRAW}.", {"ab": [], "abc": []}),
    "upper over a let name that gets a tag only upper makes": (
        _says({"upper": [{"var": "line"}]}, let={"line": _pick("<\u017fleep>Night", "Hi")}),
        "", {"ab": [], "abc": []}),
    "a launch of a module that is not an id": (
        _says("<launch:Draw now>Go!"),
        "; then Moxie starts the 'Draw now' activity.",
        {"hi": ["Moxie starts the 'Draw now' activity"]}),
    # Round 5: a tag read twice, once under a case op (its first reading hid the second);
    # a cutting op over a `let` name, read past the budget; and a split into more pieces
    # than the evaluator's value cap, which the evaluator works out all the same. Each is
    # taken out by the host, so the sentence names nothing; `test_ext_say_tags.py` checks
    # the reading ahead finds each of them.
    "a let read twice, once under upper (dotless i)": (
        _says({"concat": [{"var": "L"}, {"upper": [{"var": "L"}]}]},
              let={"L": _pick("<ex\u0131t>Bye ", "Hi")}),
        "", {"ab": [], "abc": []}),
    "the same get twice, once under upper (long s)": (
        _says({"concat": [_pick("<\u017fleep>", "Hi"), {"upper": [_pick("<\u017fleep>", "Hi")]}]}),
        "", {"ab": [], "abc": []}),
    # The DRAW the rule writes is sent and named; the lowered copy is taken out.
    "a launch read twice, once lowered": (
        _says({"concat": [_pick("<launch:DRAW>", "Hi"), {"lower": [_pick("<launch:DRAW>", "Hi")]}]}),
        f"; then sometimes {DRAW}.", {"ab": [DRAW], "abc": []}),
    "reverse of a let": (
        _says({"reverse": [{"var": "r"}]}, let={"r": "<a>tixe<b>"}), "", {"hi": []}),
    "replace over a let": (
        _says({"replace": [{"var": "t"}, "#", ":"]}, let={"t": "<launch#DRAW>Go"}),
        "", {"hi": []}),
    "a join of a split of a let": (
        _says({"join": [{"split": [{"var": "s"}, "o"]}, "i"]}, let={"s": "<exot>Bye"}),
        "", {"hi": []}),
    "two slices of lets": (
        _says({"concat": [{"slice": [{"var": "s"}, 0, 3]}, {"slice": [{"var": "t"}, 1, 4]}, "Bye"]},
              let={"s": "<exot>", "t": "xit>"}),
        "", {"hi": []}),
    "gets of single characters of a let": (
        _says({"concat": [{"get": [{"var": "w"}, i]} for i in (1, 0, 2, 4, 5, 3)] + ["Bye"]},
              let={"w": "e<x>it"}),
        "", {"hi": []}),
    "a split into 20,000 pieces, joined": (
        _says({"join": [{"split": ["<ex" + "," * 20_000 + "it>Bye", ","]}, ""]}),
        "", {"hi": []}),
    "a split into 16,000 pieces, joined (the control the evaluator works out)": (
        _says({"join": [{"split": ["<ex" + "," * 16_000 + "it>Bye", ","]}, ""]}),
        "", {"hi": []}),
}

#: The shapes whose line carries a tag the rule's text does not write whole, on at least one
#: of its inputs: the host takes it out and counts it (`ContentApp._ext_refusals`).
TAKEN_OUT = {
    "lower, which lowers the module too", "a tag split across concat parts",
    "a tag assembled from literals", "a launch of a worked-out module",
    "an always-empty part inside the tag's name",
    "a value the robot did not send inside the tag's name", "a list inside the tag's name",
    "a dotted let path inside the tag's name", "an empty part between launch and its module",
    "a tag's name split around an empty part, with its module worked out",
    "a module split around an empty part", "an and whose earlier operand is a falsy number",
    "a tag split across literal parts, past 256 lines", "a lowered launch, past 256 lines",
    "a nested concat that completes a tag, past 256 lines",
    "a part built only from literals that completes a tag, past 256 lines",
    "a let-bound line that completes a tag, past 256 lines",
    "an upper part that completes a tag, past 256 lines",
    "a trimmed part that starts with an always-empty part, then spaces",
    "lower over a get at a worked-out index",
    "upper over a let name that gets a tag only upper makes",
    "a let read twice, once under upper (dotless i)",
    "the same get twice, once under upper (long s)", "a launch read twice, once lowered",
    "reverse of a let", "replace over a let", "a join of a split of a let",
    "two slices of lets", "gets of single characters of a let",
    "a split into 20,000 pieces, joined",
    "a split into 16,000 pieces, joined (the control the evaluator works out)",
}

#: The shapes whose quote shows a tag in its pieces (`'<ex … it>Bye! …'`): another part
#: stands before the tag's first `:` or holds its `>`, and the quote shows only the literal
#: strings (`_lift_parts`). Every other shape's quote holds no `<` or `>` at all.
QUOTED_IN_PIECES = {
    "an always-empty part inside the tag's name",
    "a value the robot did not send inside the tag's name",
    "a list inside the tag's name",
    "a dotted let path inside the tag's name",
    "an empty part between launch and its module",
    "a tag's name split around an empty part, with its module worked out",
    "a nested concat that completes a tag, past 256 lines",
    "a part built only from literals that completes a tag, past 256 lines",
    "a let-bound line that completes a tag, past 256 lines",
    "an upper part that completes a tag, past 256 lines",
    "a trimmed part that starts with an always-empty part, then spaces",
}


def _names(phrase, effect):
    """True when one of the review's phrases names `effect`, an action the robot was sent
    (a launch of a worked-out module names any launch)."""
    phrase = phrase[len("sometimes "):] if phrase.startswith("sometimes ") else phrase
    return phrase == effect or (phrase == WORKED_OUT and effect.startswith("Moxie starts the "))


@pytest.mark.parametrize("shape", sorted(IMPORTED_SAYS))
def test_an_imported_say_names_what_its_tags_do_however_it_is_built(shape):
    """The review's sentence is the only place a parent learns that a line ends the chat,
    puts Moxie to sleep or starts an activity, so a line acts only on a tag the sentence
    can name: one written whole in the rule's own text. Each program runs as an imported
    global with only the default grants: the sentence (in `explain()` and in the pack
    review) names what the robot is sent, "sometimes" when not every line it can say does
    it, and holds no tag (only the pieces of one split around another part,
    `QUOTED_IN_PIECES`); a tag the line built is taken out before the line is kept, never
    said and never acted on, and counted (`TAKEN_OUT`). Read again with nothing left to
    build (from the program's own text alone), the sentence still names what the robot is
    sent."""
    program, then, heard = IMPORTED_SAYS[shape]
    assert E.validate(program, grants=E.DEFAULT_GRANTS) == [], shape
    (sentence,) = E.explain(program)
    assert sentence.endswith(then) if then else "; then" not in sentence, sentence
    assert ("<" in sentence or ">" in sentence) == (shape in QUOTED_IN_PIECES), sentence
    assert tag_names(sentence) == [], sentence
    assert sentence in P.extension_warnings({"extension": program})
    with _past_the_budget():
        (cheap,) = E.explain(program)
    module = dict(CHAT_MODULE, globals=[{"name": "Probe", "pattern": r"\w",
                                         "extension": program}])
    taken_out = 0
    for speech, want in heard.items():
        brain = Brain()
        app = app_with(module, chat=brain)
        reply = app.respond(Turn(robot=ext_robot(), speech=speech))
        assert brain.turns == [] and reply.text != QUESTION, (shape, speech)
        assert tag_names(reply.text) == [], (shape, speech, reply.text)
        taken_out += sum(app._ext_refusals.values())
        assert not app._ext_breaches, "a taken-out tag is not a breach"
        sent = [_effect_of(a) for a in reply.actions]
        assert sent == want, (shape, speech, reply)
        for read in (sentence, cheap):
            named = _named_in(read)
            # What the robot was sent is named; what is named without "sometimes" always is.
            assert all(any(_names(n, e) for n in named) for e in sent), (sent, read)
            assert all(any(_names(n, e) for e in sent)
                       for n in named if not n.startswith("sometimes ")), (sent, read)
    assert (taken_out > 0) == (shape in TAKEN_OUT), (shape, taken_out)


#: Parts worked out at run time that come out as nothing on some turns or all of them, and
#: as text without a `<`, `:` or `>` on the others.
_RUN_TIME_PARTS = {
    "an always-empty path": _ALWAYS_EMPTY,
    "a value the robot did not send": _NOT_SENT,
    "a list": _A_LIST,
    "what the child said": {"var": "speech"},
    "an if, empty or z": {"if": [{"contains": [{"var": "speech"}, "x"]}, "", "z"]},
    "an if, a space or empty": {"if": [{"contains": [{"var": "speech"}, "x"]}, " ", ""]},
    "an and that stops at 0": {"and": [{"len": [_NOT_SENT]}, "q"]},
    "a nested concat": {"concat": [_ALWAYS_EMPTY]},
    "an upper": {"upper": [_ALWAYS_EMPTY]},
}


#: The tags the sweeps split, spaces and fields included.
_TAGS = ("<exit>", "<sleep>", "<launch:DRAW>", "<launch:DRAW:story>", "< EXIT >",
         "<launch : DRAW : >", "<launch_if_confirmed:DRAW>")


def _split_tags():
    """Every tag split at every point around one worked-out part, and the shorter ones at
    every two points around an always-empty part and what the child said, in both orders."""
    for tag in _TAGS:
        for at in range(1, len(tag)):
            for kind, part in _RUN_TIME_PARTS.items():
                yield f"{tag} split at {at} by {kind}", [tag[:at], part, tag[at:] + "Bye"]
        if len(tag) > 19:
            continue
        for at, to in itertools.combinations(range(1, len(tag)), 2):
            for first, second in ((_ALWAYS_EMPTY, {"var": "speech"}),
                                  ({"var": "speech"}, _ALWAYS_EMPTY)):
                yield (f"{tag} split at {at} and {to}",
                       [tag[:at], first, tag[at:to], second, tag[to:] + "!"])


def _named_in(sentence):
    """The phrases after "; then", one for each effect the sentence names."""
    return (sentence.partition("; then ")[2].rstrip(".").split(" and ")
            if "; then " in sentence else [])


def _sweep(name, program, speeches=("hi", "x marks")):
    """`program`'s sentence, and the one it reads as with nothing left to build (from its
    own text alone), each name whatever the robot is sent for each of `speeches`, say
    without "sometimes" only what it is sent every time, and hold no whole tag."""
    assert E.validate(program, grants=E.DEFAULT_GRANTS) == [], name
    (sentence,) = E.explain(program)
    with _past_the_budget():
        (cheap,) = E.explain(program)
    app = app_with(dict(CHAT_MODULE, globals=[
        {"name": "Probe", "pattern": r"\w", "extension": program}]), chat=Brain())
    replies = {speech: app.respond(Turn(robot=ext_robot(), speech=speech))
               for speech in speeches}
    for read in (sentence, cheap):
        assert tag_names(read) == [], (name, read)
        named = _named_in(read)
        for speech, reply in replies.items():
            assert reply.text != QUESTION, (name, speech)
            sent = [_effect_of(a) for a in reply.actions]
            where = (name, speech, sent, read)
            assert all(any(_names(n, e) for n in named) for e in sent), where
            assert all(any(_names(n, e) for e in sent)
                       for n in named if not n.startswith("sometimes ")), where


def test_a_tag_split_around_a_part_worked_out_at_run_time_is_named_however_it_is_split():
    """A worked-out part can come out as nothing, and a tag written around it then forms
    from the program's own text: a sweep of 1,612 splits, each read directly and after nine
    `if`s that take it past 256 lines, and each run through the real `ContentApp` twice.
    Whatever the robot is sent, the review names; what it names without "sometimes", the
    robot is sent every time; and no sentence holds a whole tag. The same holds for each
    one read with nothing left to build."""
    shapes = list(_split_tags())
    assert len(shapes) == 1612
    for name, parts in shapes:
        for capped in (False, True):
            _sweep(f"{name}{', past 256 lines' if capped else ''}",
                   _says({"concat": (_NINE_IFS if capped else []) + parts}))


def _trimmed_splits():
    """Every tag split at every point, one piece inside a `trim` beside an always-empty part
    and two spaces: once that part comes out as nothing, `trim` takes the spaces too, and
    the tag forms."""
    for tag in _TAGS:
        for at in range(1, len(tag)):
            yield (f"{tag} split at {at}, the rest trimmed after an empty part",
                   [tag[:at], {"trim": [{"concat": [_ALWAYS_EMPTY, "  " + tag[at:] + "Bye"]}]}])
            yield (f"{tag} split at {at}, the start trimmed before an empty part",
                   [{"trim": [{"concat": [tag[:at] + "  ", _ALWAYS_EMPTY]}]}, tag[at:] + "!"])


def test_a_tag_split_around_a_trimmed_part_is_named_however_it_is_split():
    """`trim` reads a worked-out part at an end, with the spaces beside it, as one part that
    may come out as nothing (round 4: it read them as text, so `"<ex"` and a trimmed
    `[nothing, "  it>Bye"]` named nothing while the robot was sent EXIT). 180 splits, each
    run through the real `ContentApp`, checked as the 1,612-split sweep is."""
    shapes = list(_trimmed_splits())
    assert len(shapes) == 180
    for name, parts in shapes:
        _sweep(name, _says({"concat": parts}), speeches=("hi",))


def test_a_random_pick_among_computed_lines_names_its_tag_as_sometimes():
    """`random.pick` over a `list` op (not a fixed `lit` list): the review says "sometimes",
    and across turns the robot is sent the EXIT on some and nothing on others."""
    program = _says({"random.pick": [{"list": ["<exit>Bye", "Hi"]}]},
                    caps=("handled", "random", "say"))
    (sentence,) = E.explain(program)
    assert sentence.endswith("; then sometimes the conversation ends."), sentence
    module = dict(CHAT_MODULE, globals=[{"name": "Probe", "pattern": r"\w",
                                         "extension": program}])
    app = app_with(module, chat=Brain(), ext_grants=E.DEFAULT_GRANTS | {"random"},
                   clock=lambda: 1_700_000_000)
    sent = {tuple(_effect_of(a) for a in app.respond(
        Turn(robot=ext_robot(), speech=f"turn {n}")).actions) for n in range(12)}
    assert sent == {(), ("the conversation ends",)}, sent


def test_every_op_explain_does_not_follow_holds_no_text_or_passes_a_tag_on():
    """`_Reader` follows a few ops line by line. Of the rest, `_NO_TEXT_OPS` yield numbers
    and yes/no, and every other op may hand an argument's text on, so a tag in its
    arguments is read as "sometimes". A new op has to be put on one side on purpose."""
    from moxie_sdk.content.ext.explain import _CASE, _NO_TEXT_OPS
    followed = {"if", "and", "or", "concat", "random.pick"} | set(_CASE)
    assert followed <= set(E.OPS) and _NO_TEXT_OPS <= set(E.OPS)
    assert not followed & _NO_TEXT_OPS
    assert set(E.OPS) - followed - _NO_TEXT_OPS == {
        "slice", "replace", "split", "join", "repeat", "format", "plural", "list", "get",
        "compact", "reverse", "sort", "keys"}


#: One character that takes four bytes, so every character of a line holding it does too.
_WIDE = "\U0001F600"
#: 16,000 characters, one of them `_WIDE`: as long as one value may be.
_SIXTEEN_K = "x" * 15_999 + _WIDE


def _seven_ifs():
    """Seven `if`s over two 1,100-character lines each, with `_WIDE` in every one: a `concat`
    of them can say 128 lines of 7,700 characters, about a million in all."""
    return {"concat": [{"if": [{"var": "speech"},
                               ("ab" * 550)[:1099] + _WIDE + str(i),
                               ("cd" * 550)[:1099] + _WIDE + str(i)]} for i in range(7)]}


def _doubling():
    """Thirty `let` names that each double the lines of the last (2^30 read one by one)."""
    chain = {"a0": {"if": [{"var": "speech"}, "<exit>Bye", "Hi"]}}
    for k in range(1, 30):
        chain[f"a{k}"] = {"if": [{"var": "speech"}, {"var": f"a{k - 1}"},
                                 {"concat": [{"var": f"a{k - 1}"}, "!"]}]}
    return _says({"var": "a29"}, let=chain)


def _upper_copies():
    """A thousand names that are each `upper` of one ~1M-character name (the verifier's
    first shape; on the previous head 6.8 s and 3.9 GB, measured)."""
    chain = {"a0": _seven_ifs()}
    for k in range(1, 1000):
        chain[f"a{k}"] = {"upper": [{"var": "a0"}]}
    return _says({"concat": ["<exit>", {"var": "a999"}]}, let=chain)


def _upper_or_lower():
    """Four hundred names that are each an `if` over `upper` and `lower` of the one before
    (the verifier's second shape; on the previous head 16.5 s and 3.1 GB, measured)."""
    chain = {"a0": _seven_ifs()}
    for k in range(1, 400):
        chain[f"a{k}"] = {"if": [{"var": "speech"}, {"upper": [{"var": f"a{k - 1}"}]},
                                 {"lower": [{"var": f"a{k - 1}"}]}]}
    return _says({"concat": [{"var": "a399"}, "<exit>"]}, let=chain)


def _growing_upper():
    """A hundred names that are each `upper` of the one before and 16,000 more characters
    (the verifier's third shape; on the previous head it ran out of memory after 12.6 s, at
    7.7 GB under an 8 GB cap, measured)."""
    big = {"repeat": ["x" * 999 + _WIDE, 16]}
    chain = {"a0": {"concat": ["<exit>", {"var": "speech"}]}}
    for k in range(1, 100):
        chain[f"a{k}"] = {"upper": [{"concat": [{"var": f"a{k - 1}"}, big]}]}
    return _says({"var": "a99"}, let=chain)


def _deep_nest():
    """29 nested `concat`s of 31 parts that each work out to 16,000 characters, over what
    the child said (900 KB)."""
    big = {"repeat": ["x" * 1000, 16]}
    deep = {"concat": [big] * 31 + [{"var": "speech"}]}
    for _ in range(28):
        deep = {"concat": [big] * 31 + [deep]}
    return _says(deep)


#: Programs built to make `explain()` multiply what it reads or builds, and how each one's
#: sentence ends ("" when it names nothing).
BUILT_TO_MULTIPLY = {
    "thirty names that each double the lines of the last": (
        _doubling, "; then sometimes the conversation ends."),
    "a concat of 32 ifs": (
        lambda: _says({"concat": [{"if": [{"var": "speech"}, "<sleep>z", "z"]}] * 32}),
        "; then sometimes Moxie goes to sleep."),
    "a < and then 992 worked-out parts": (
        lambda: _says({"concat": ["<"] + [{"concat": [{"var": "speech"}] * 32}] * 31}), ""),
    "a thousand names that are each upper of a million-character name": (
        _upper_copies, "; then sometimes the conversation ends."),
    "four hundred names that are each upper or lower of the one before": (
        _upper_or_lower, "; then sometimes the conversation ends."),
    "a hundred names that each add 16,000 characters under upper": (
        _growing_upper, "; then sometimes the conversation ends."),
    "29 nested concats of 31 parts that each work out to 16,000 characters": (
        _deep_nest, ""),
    "a quoted line with 200,000 spaces after <exit: and no >": (
        lambda: _says("<exit:" + " " * 200_000 + "x"), ""),
    # Parts made only of literals are worked out by the evaluator, which builds a value
    # before it refuses one over its cap (and counts an empty string as nothing).
    "a replace that would build 256 million characters": (
        lambda: _says({"replace": [_SIXTEEN_K, "x", _SIXTEEN_K]}), ""),
    "a join that would build 256 million characters": (
        lambda: _says({"join": [{"split": ["," * 16_000, ","]}, _SIXTEEN_K]}), ""),
    "three hundred names that each split 900,000 commas": (
        lambda: _says({"var": "a299"}, let=dict(
            {"commas": "," * 900_000},
            **{f"a{k}": {"split": [{"var": "commas"}, ","]} for k in range(300)})), ""),
}


@pytest.mark.parametrize("shape", sorted(BUILT_TO_MULTIPLY))
def test_a_say_built_to_multiply_is_read_in_bounded_time_and_memory(shape):
    """Each program reads in under 5 s (a hard alarm) and under 64 MB (tracemalloc's peak),
    and its sentence still names its tag. Read one by one, the first would be 2^30 lines and
    the second 2^32: past 256 lines a `say` is read in its parts. The names of the next three
    each hold a copy of a ~1M-character line or of every text before them: everything
    `explain()` builds counts against one budget, and past it a `say` is read from the
    program's own text in one pass (before it, 6.8 s and 3.9 GB, 16.5 s and 3.1 GB, and out
    of memory at 7.7 GB). A `<` followed by 992 worked-out parts is looked for in one pass (a
    regex with three neighbouring repeats that each take a worked-out part took 11.2 s on
    it), and so is the quoted line (the lazy fields `actions._TAG_RE` had until round 7 took
    59.7 s on it, four times as long per doubling). The last three are parts made of literals, which `explain()` works
    out with the evaluator: it now sizes each op before it builds anything (before, the first
    two peaked at 1 GB and the third was still reading after 120 s). All measured on the
    build host."""
    build, then = BUILT_TO_MULTIPLY[shape]
    program = build()
    assert E.validate(program, grants=E.DEFAULT_GRANTS) == []
    tracemalloc.start()
    try:
        with _hard_limit(5.0):
            (sentence,) = E.explain(program)
        peak = tracemalloc.get_traced_memory()[1]
    except _Stalled:
        pytest.fail("explain() was still reading after 5 s")
    finally:
        tracemalloc.stop()
    assert peak < 64 * 2 ** 20, f"explain() peaked at {peak / 2 ** 20:.0f} MB"
    assert sentence.endswith(then) if then else "; then" not in sentence, sentence


def test_a_deep_nest_of_large_literal_parts_is_joined_a_bounded_length_at_a_time():
    """Past 256 lines each `concat` is read as one text, the text of every `concat` inside it
    included. A 900 KB pack of 29 nested `concat`s, each of 31 parts that work out to 16 KB,
    over what the child said, would make that text grow level by level (500 MB at the peak
    and 1.8 s, measured without the bound). Past a million characters a `concat` is read in
    its parts only, which no turn could say or pass on under the evaluator's default caps.
    Read here with room to build all of it; under `explain()`'s own budget the same program
    is read from its own text (`test_a_say_built_to_multiply_is_read_in_bounded_time_and_memory`)."""
    from moxie_sdk.content.ext.explain import _MAX_CHARS, _Reader
    deep = _deep_nest()["rules"][0]["do"][0]["say"]
    assert max(len(text) for text in _Reader({}, [10 ** 12]).texts_in(deep)) <= _MAX_CHARS


def test_a_line_too_long_to_say_names_what_it_writes_and_a_sentence_names_16_activities():
    """An extension's line longer than 1,000 characters (`MAX_SAY_CHARS`) is refused whole,
    and the turn goes on without the extension: its tags never reach the robot. The review
    still names what the line writes, as "sometimes" (it named each as certain before
    round 4, and nothing at all from round 4 to round 5: a tag written whole is now always
    named, since it is what the robot may act on). A line that can be said may start many
    activities: the sentence names the first 16 and "sometimes Moxie starts an activity it
    works out" for the rest."""
    twenty = "".join(f"<launch:A{i}>" for i in range(20)) + "Go!"
    for line, then, sent in (
            (twenty + "!" * 1000, "; then " + " and ".join(
                [f"sometimes Moxie starts the A{i} activity" for i in range(16)]
                + [f"sometimes {WORKED_OUT}"]) + ".", []),
            (twenty, "; then " + " and ".join(
                [f"Moxie starts the A{i} activity" for i in range(16)] + [f"sometimes {WORKED_OUT}"]) + ".",
             [f"Moxie starts the A{i} activity" for i in range(20)])):
        program = _says(line)
        (sentence,) = E.explain(program)
        assert sentence.endswith(then) if then else "; then" not in sentence, sentence
        brain = Brain()
        module = dict(CHAT_MODULE, globals=[{"name": "Probe", "pattern": r"\w",
                                             "extension": program}])
        reply = app_with(module, chat=brain).respond(Turn(robot=ext_robot(), speech="hi"))
        assert [_effect_of(a) for a in reply.actions] == sent, reply
        assert all(any(_names(n, e) for n in _named_in(sentence)) for e in sent)


def test_past_64_different_tags_an_op_may_hand_on_the_ones_written_are_still_named():
    """An op `explain()` does not follow (`get` here) may hand on any tag written in what it
    reads. Past 64 different ones the reading ahead keeps none of them one by one, but the
    sentence still names what the rule writes whole, all 70 launches as "sometimes" (16
    activities by name, then the rest). It no longer names an exit or a sleep there: the
    rule writes neither, so the robot can be sent neither."""
    program = _says({"get": [{"lit": [f"<launch:A{i}>Go" for i in range(70)]},
                             {"len": [{"var": "speech"}]}]})
    (sentence,) = E.explain(program)
    assert _named_in(sentence) == (
        [f"sometimes Moxie starts the A{i} activity" for i in range(16)]
        + [f"sometimes {WORKED_OUT}"]), sentence
    module = dict(CHAT_MODULE, globals=[{"name": "Probe", "pattern": r"\w",
                                         "extension": program}])
    for speech, module_id in (("ab", "A2"), ("x" * 40, "A40")):
        reply = app_with(module, chat=Brain()).respond(Turn(robot=ext_robot(), speech=speech))
        sent = [_effect_of(a) for a in reply.actions]
        assert sent == [f"Moxie starts the {module_id} activity"], reply
        assert all(any(_names(n, e) for n in _named_in(sentence)) for e in sent)


def test_the_quote_lifts_exactly_the_tags_a_robot_acts_on_in_one_pass():
    """explain.py restates the tag regex to lift tags out of a quote (its package imports
    nothing outside itself). On every line of the drift corpus and 20,000 random ones it
    lifts exactly the tags `actions._TAG_RE` finds whose name acts, and nothing else."""
    from moxie_sdk.actions import _TAG_RE, KNOWN_TAGS
    from moxie_sdk.content.ext.explain import _lift
    pool = ["<", ">", ":", " ", "\t", "\u00a0", "exit", "EXIT", "eXit", "sleep", "\u017fleep",
            "launch", "_if_confirmed", "DRAW", "x", "2", "\ue000", "<exit>", "<launch:"]
    rng = random.Random(4)
    lines = ["<exit>Bye!", "< sleep >zz", "<launch : DRAW : >go", "<exit:now>hm", "<<exit>>x",
             "<launch:DR<exit>AW>x", "<exitx>no", "<exit\u00a0>nb", "<opener>Hi"]
    lines += ["".join(rng.choice(pool) for _ in range(rng.randint(1, 12)))
              for _ in range(20_000)]
    for line in lines:
        want = _TAG_RE.sub(lambda m: " " if m.group(1).lower() in KNOWN_TAGS else m.group(0),
                           line)
        assert _lift(line) == want, repr(line)


def test_a_test_on_what_the_child_said_shows_a_tag_as_written():
    """Only a line Moxie says loses its tags (the sentence says what they do): a test that
    compares what the child said with "<exit>" quotes it as written. When every quote lost
    its tags it read "When what your child said is an empty phrase"."""
    asks = {"==": [{"var": "speech"}, "<exit>"]}
    (sentence,) = E.explain(_imported({"when": asks, "do": [{"say": "Okay!"},
                                                             {"handled": True}]}))
    assert sentence == ("When what your child said is '<exit>': tells your child 'Okay!' "
                        "and answers without asking the AI."), sentence
    (sentence,) = E.explain(_says({"if": [asks, "<exit>Bye!", "Hi"]}))
    assert sentence == ("Whenever this activity is triggered: tells your child 'Bye!' when "
                        "what your child said is '<exit>', otherwise 'Hi' and answers without "
                        "asking the AI; then sometimes the conversation ends."), sentence


# --------------------------------------------------------------------------- #
# What the content brain is told
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name,module_id,content_id", [
    ("starter.json", "FREE_CHAT", "default"),
    ("memory_chat.json", "MEMORY_CHAT", "default"),
    ("memory_chat.json", "MEMORY_CHAT", "aboutme"),
])
def test_the_content_brain_is_taught_exit_and_sleep_once_and_never_launch(
        name, module_id, content_id):
    brain = Brain()
    history = [{"role": "user", "content": "I have a dog"},
               {"role": "assistant", "content": "A dog! What is its name?"}]
    shipped_app(name, brain).respond(Turn(robot=robot(module_id, content_id),
                                          speech="tell me about whales", history=history))
    (messages,) = brain.turns
    assert [m["role"] for m in messages].count("system") == 1
    assert messages[0]["role"] == "system"
    assert messages[1:] == history + [{"role": "user", "content": "tell me about whales"}]
    system = messages[0]["content"]
    assert system.count(EXIT_LINE) == 1 and system.count(SLEEP_LINE) == 1
    assert "<launch" not in system, "this brain is never told a module id to launch"
    # Persona, then the module's prompt, then the tags: "described below" is now true.
    assert system.startswith(DEFAULT_PERSONA)
    assert system.index("Sam") < system.index(EXIT_LINE)
    assert system.index("described below") < system.index(EXIT_LINE)


#: sha256 of what the llm brain is told, measured on origin/dev (4c409b6) before this
#: change. Its prompt was measured live (test_live_action_tags.py), so it must not move.
LLM_GOLDEN = {
    "DEFAULT_PERSONA": "2cfb28a8fe3956664ce29c349fd75ccc558fabd14445469eea177a2efac4e5c2",
    "expressive": "5b2664a291ed9b0be5d1334a8caf8317ffb9a022ee779ea85afa66b8aed92bf0",
    "expressive/presence": "bc08be0805b5f318e35b15c75f3331ad32f1b883077651ab699929429818e8b9",
    "plain": "19825315d7f4141e9624242b4c6ad1e7d8a7a7d36a5c9465105f951f7517b22b",
    "plain/presence": "3383557c088d74432b2d8fd048ec33d74c7e4e852aa3362e23dd080cb9a872f0",
}


def test_the_llm_brain_prompt_and_persona_are_byte_identical_to_origin_dev():
    def sha(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    got = {"DEFAULT_PERSONA": sha(DEFAULT_PERSONA)}
    for label, expressive in (("expressive", True), ("plain", False)):
        app = LLMApp("http://127.0.0.1:1/v1", "unused", client=object(),
                     expressive=expressive)
        sam = RobotContext(device_id="d1", child=ChildProfile(
            nickname="Sam", pronouns="she/her", notes="likes dinosaurs"))
        got[label] = sha(app._system(sam))
        robin = RobotContext(device_id="d2", child=ChildProfile(nickname="Robin"))
        turn = Turn(robot=robin, speech="hi",
                    presence={"line": "A child has just come into view."})
        got[f"{label}/presence"] = sha(app._system(robin, turn))
    assert got == LLM_GOLDEN


# --------------------------------------------------------------------------- #
# Through the real runtime
# --------------------------------------------------------------------------- #

def _runtime(app, tmp_path, module_id="MEMORY_CHAT"):
    rt, did = make_runtime(app, module_id=module_id, store=JsonStore(str(tmp_path / "rt")))
    rt.client = LatchClient(runtime=rt)
    rt.brain_budget_s = 0                  # no filler race: every answer here is instant
    return rt, did


def _say(rt, did, speech, *, command="prompt", event_id="evt", **extra):
    """One turn; waits for its published reply (the pool stays up for the next one)."""
    topic = CHAT_TOPIC.format(device_id=did)
    before = len(rt.client.on(topic))
    payload = dict(extra, command=command, backend="router", event_id=event_id,
                   speech=speech)
    rt._on_remote_chat(did, rt.robots[did], json.dumps(payload))
    assert rt.client.wait_for(
        lambda pubs: sum(1 for t, _ in pubs if t == topic) > before, timeout=15), \
        f"no reply published for {command} {speech!r}"
    return rt.client.on(topic)[-1]


SUMMARY = json.dumps({"facts": ["Sam has a dog named Pepper"], "preferences": [],
                      "open_threads": [], "summary": "They talked about pets."})


def test_bye_through_the_runtime_ends_the_conversation_and_writes_the_summary(tmp_path):
    """The whole point: the brain never writes <exit>, yet the goodbye ends the
    conversation and the memory summary is written (on origin/dev it waited for a
    disconnect)."""
    brain = Brain(summary=SUMMARY)
    app = shipped_app("memory_chat.json", brain,
                      memory=MemoryStore(JsonStore(str(tmp_path / "mem"))))
    ended = []
    real_end = app.on_session_end

    def spy(robot_ctx, history, reason=""):
        ended.append(reason)
        return real_end(robot_ctx, history, reason)

    app.on_session_end = spy
    rt, did = _runtime(app, tmp_path)
    _say(rt, did, "I have a dog", event_id="e1")
    _say(rt, did, "her name is Pepper", event_id="e2")
    bye = _say(rt, did, "Bye, Moxie!", event_id="e3")
    rt._pool.shutdown(wait=True)

    assert len(brain.turns) == 2, "the goodbye itself must not cost a model call"
    assert bye["response_actions"] == [encode_action(Action(type=ActionType.EXIT))], bye
    assert bye["output"]["text"] and "<exit>" not in bye["output"]["text"]
    assert ended == ["exit"]
    block = app.memory.load(did)["memory_chat"]
    assert block["_meta"]["summarized_through"] == len(rt.history[did]) == 6
    assert [item_text(f) for f in block["facts"]] == ["Sam has a dog named Pepper"]
    assert block["_provenance"][0]["reason"] == "exit"


class SleepyBrain(Brain):
    """Like `Brain`, but a turn about a nap gets a model line that starts with <sleep>."""

    def __call__(self, messages):
        if messages[0].get("role") == "system" and "nap" in messages[-1]["content"]:
            self.turns.append(messages)
            return "<sleep>Okay, nap time! Sweet dreams."
        return super().__call__(messages)


@pytest.mark.parametrize("speech,model_calls", [
    ("Moxie, go to sleep.", 2),           # the Sleep global: no model call
    ("I'm sleepy, can you take a nap?", 3),   # the brain writes <sleep> itself
], ids=["sleep-global", "brain-sleep-tag"])
def test_going_to_sleep_ends_the_conversation_and_writes_the_summary(
        tmp_path, speech, model_calls):
    """When Moxie goes to sleep the session is over, so a SLEEP ends the conversation and
    writes the summary, as an EXIT does (decided by the owner; before, the summary waited
    for a disconnect or a module switch)."""
    brain = SleepyBrain(summary=SUMMARY)
    app = shipped_app("memory_chat.json", brain,
                      memory=MemoryStore(JsonStore(str(tmp_path / "mem"))))
    ended = []
    real_end = app.on_session_end

    def spy(robot_ctx, history, reason=""):
        ended.append(reason)
        return real_end(robot_ctx, history, reason)

    app.on_session_end = spy
    rt, did = _runtime(app, tmp_path)
    _say(rt, did, "I have a dog", event_id="e1")
    _say(rt, did, "her name is Pepper", event_id="e2")
    night = _say(rt, did, speech, event_id="e3")
    rt._pool.shutdown(wait=True)

    assert len(brain.turns) == model_calls
    assert night["response_actions"] == [encode_action(Action(type=ActionType.SLEEP))]
    assert ended == ["sleep"]
    block = app.memory.load(did)["memory_chat"]
    assert block["_meta"]["summarized_through"] == len(rt.history[did]) == 6
    assert [item_text(f) for f in block["facts"]] == ["Sam has a dog named Pepper"]
    assert block["_provenance"][0]["reason"] == "sleep"


def test_go_to_sleep_through_the_runtime_publishes_the_sleep_action(tmp_path):
    brain = Brain()
    rt, did = _runtime(shipped_app("starter.json", brain), tmp_path, module_id="FREE_CHAT")
    reply = _say(rt, did, "Moxie, go to sleep.")
    rt._pool.shutdown(wait=True)
    assert brain.turns == []
    assert reply["response_actions"] == [encode_action(Action(type=ActionType.SLEEP))]


def _openers(nickname="Sam"):
    """MEMORY_CHAT's two openers, rendered for `nickname`."""
    conv = next(c for c in _raw("memory_chat.json")["conversations"]
                if c["module_id"] == "MEMORY_CHAT" and c["content_id"] == "default")
    return [alt.replace("{{ volley.config.child_pii.nickname }}", nickname)
            for alt in conv["opener"].split("|")]


def test_an_empty_prompt_speaks_the_module_opener(tmp_path):
    """No model call; staged, voiced and remembered exactly like any reply."""
    brain = Brain()
    rt, did = _runtime(shipped_app("memory_chat.json", brain), tmp_path)
    synth = CountingSynth()
    rt.set_synthesizer(synth)
    reply = _say(rt, did, "")
    rt._pool.shutdown(wait=True)
    text = reply["output"]["text"]
    assert brain.turns == [], "the opener must not cost a model call"
    assert text == _openers()[0], "a robot hears the first opener first"
    # one plain closing reply, in the envelope the wire encoder spells for any line: no
    # chunk, no action, only the runtime's vision subscription
    plain = build_chat_response("evt", text, subscribe_events=list(presence_seam.VISION_EVENTS))
    assert ({k: v for k, v in reply.items() if k != "output"}
            == {k: v for k, v in plain.items() if k != "output"})
    # the same staging as a model line: performed markup and scored fields
    assert reply["output"]["markup"] != text and "<mark" in reply["output"]["markup"]
    assert {"mood", "dialog_act"} <= set(reply["output"])
    assert synth.spoken == [text]
    assert rt.history[did] == [{"role": "assistant", "content": text}]


def test_twenty_empty_prompts_rotate_the_openers(tmp_path):
    brain = Brain()
    rt, did = _runtime(shipped_app("memory_chat.json", brain), tmp_path)
    said = [_say(rt, did, "", event_id=f"e{i}")["output"]["text"] for i in range(20)]
    rt._pool.shutdown(wait=True)
    assert brain.turns == []
    assert set(said) == set(_openers()), said
    assert all(a != b for a, b in zip(said, said[1:])), said
    assert not any("<opener>" in s for s in said)


#: The spoken half of every reply below, measured on origin/dev (4c409b6) before this
#: change: the fake brain's line as the runtime staged it, i.e. its performed markup and the
#: scored fields `_publish_chat` hands the encoder. The opener path must not move it.
#: `_golden_reply` wraps it with `wire.build_chat_response`, so a wire-spelling change (the
#: `result` code, the `signals` shape) moves the expectation with the encoder.
GOLDEN_STAGED = {
    "dialog_act": "statement_non_opinion",
    "emotion": "surprise",
    "markup": (
        '<mark name="cmd:playback-mood,data:{+mood+:5,+intensity+:1}"/>Ooh,'
        '<break time="0.35s"/><mark name="cmd:playback-mood,data:{+mood+:1,+intensity+:1}"/>'
        '<mark name="cmd:behaviour-tree,data:{+transition+:0.5,+duration+:1.0,+repeat+:1,'
        '+blocking+:false,+action+:0,+eventName+:+Gesture_Self+,+category+:+BehaviourTree+,'
        '+behaviour+:++,+Track+:++}"/> <usel variant="0" genre="excited">tell me more about '
        'that!</usel><mark name="cmd:behaviour-tree,data:{+transition+:0.5,+duration+:1.0,'
        '+repeat+:1,+blocking+:false,+action+:0,+eventName+:+Gesture_None+,'
        '+category+:+BehaviourTree+,+behaviour+:++,+Track+:++}"/>'),
    "mood": "surprised",
    "mood_intensity": 1,
    "signal": "no_signal",
    "text": "Ooh, tell me more about that!",
}


def _golden_reply(event_id):
    """What origin/dev published for `GOLDEN_STAGED`: one plain reply with no action, only
    the runtime's vision subscription, as the wire encoder spells it."""
    g = GOLDEN_STAGED
    return build_chat_response(event_id, g["text"], g["markup"], mood=g["mood"],
                               mood_intensity=g["mood_intensity"], dialog_act=g["dialog_act"],
                               emotion=g["emotion"], signals=g["signal"],
                               subscribe_events=list(presence_seam.VISION_EVENTS))


NO_OPENER = {"conversations": [{
    "name": "Chat", "module_id": "CHAT", "content_id": "default",
    "prompt": "You are Moxie talking to {{ volley.config.child_pii.nickname }}."}]}


@pytest.mark.parametrize("command,speech,extra,heard", [
    ("continue", "", {}, ""),
    ("reprompt", "", {}, ""),
    ("prompt", "I built a fort today", {}, "I built a fort today"),
    ("prompt", "", {"extra_lines": [{"context_type": "input",
                                     "text": "can we talk about space"}]},
     "can we talk about space"),
    ("prompt", "", {"module": NO_OPENER}, ""),
], ids=["continue-empty", "reprompt-empty", "prompt-speech", "prompt-extra-lines",
        "prompt-empty-no-opener"])
def test_every_other_turn_publishes_what_origin_dev_published(
        tmp_path, command, speech, extra, heard):
    """Golden: the opener changes ONE case. Everything else is byte-identical to origin/dev
    — the published reply, what was voiced, what was remembered, and the child's side of
    what the brain was sent (its system message gains the tags by design)."""
    extra = dict(extra)
    module = extra.pop("module", None)
    brain = Brain(answer=GOLDEN_STAGED["text"])
    if module is None:
        app, module_id = ContentApp(load_modules(_raw("memory_chat.json")), brain,
                                    persona="P", memory=False), "MEMORY_CHAT"
    else:
        app, module_id = ContentApp(load_modules(module), brain, persona="P",
                                    memory=False), "CHAT"
    rt, did = _runtime(app, tmp_path, module_id=module_id)
    synth = CountingSynth()
    rt.set_synthesizer(synth)
    reply = _say(rt, did, speech, command=command, event_id="evt-gold", **extra)
    rt._pool.shutdown(wait=True)

    assert len(rt.client.chat_replies(did)) == 1
    assert reply == _golden_reply("evt-gold")
    assert synth.spoken == [GOLDEN_STAGED["text"]]
    said = [{"role": "user", "content": heard}] if heard else []
    assert rt.history[did] == said + [{"role": "assistant", "content": GOLDEN_STAGED["text"]}]
    assert [m[1:] for m in brain.turns] == [[{"role": "user", "content": heard}]]


#: Openers the content preview route (`POST /content/render`) used to return differently
#: from what the robot says: it split on every `|` and left the tags in.
PREVIEW_OPENERS = [
    "Hi {{ volley.config.child_pii.nickname | upper }}! Ready?|Hey!",
    "{% if volley.config.child_pii.nickname | length > 2 %}Hi "
    "{{ volley.config.child_pii.nickname }}!{% endif %}|Hi!",
    "{# shown | to the parent #}Let's draw!<launch:DRAW>|Or not.",
    "<exit>Bye for now!<opener>|See you!",
    "Okay, sleepy time.<sleep>",
    "{{ '' }}|The second one is the first heard.",
    # an exit the template builds around a sleep: the robot path lifts every level of it
    "{{ '<ex' }}<sleep>{{ 'it>' }}Night night.",
    # and a shipped one, which both always agreed on
    next(c for c in _raw("memory_chat.json")["conversations"]
         if c["content_id"] == "default")["opener"],
]


@pytest.mark.parametrize("opener", PREVIEW_OPENERS, ids=[
    "filter", "if-filter", "comment-launch", "exit", "sleep", "empty-first", "nested-pieces",
    "shipped"])
def test_the_content_preview_route_returns_the_line_a_robot_hears_first(tmp_path, opener):
    """The route splits and lifts tags as the robot path does (`pick_opener`), and a
    robot's rotation never leaks into it. (The console's editor card shows only the
    prompt from this route, not the opener.)"""
    from moxie_sdk.content.content_app import opener_alternatives
    conv = {"name": "Chat", "module_id": "CHAT", "content_id": "default",
            "prompt": "You are Moxie.", "opener": opener}
    app = ContentApp(load_modules({"conversations": [conv]}), Brain(), memory=False)
    rt, did = _runtime(app, tmp_path, module_id="CHAT")

    def preview():
        return rt.content_render({"kind": "conversation", "data": conv,
                                  "context": {"nickname": "Sam"}})

    before = preview()
    heard = [_say(rt, did, "", event_id=f"e{i}")["output"]["text"] for i in range(2)]
    rt._pool.shutdown(wait=True)
    assert before["opener"] == heard[0] and "<" not in heard[0], (before, heard)
    assert before["openers"] == [a for a in opener_alternatives(opener) if a.strip()]
    assert preview() == before
