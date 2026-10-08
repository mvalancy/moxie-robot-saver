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
import json
import os
import re
import signal
import threading
import time

import pytest

from helpers_ext import CHAT_MODULE, app_with, robot as ext_robot
from helpers_runtime import CHAT_TOPIC, CountingSynth, LatchClient, make_runtime
from moxie_sdk import presence as presence_seam
from moxie_sdk.actions import ACTION_TAG_PROMPT, parse_action_tags
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


class _Stalled(Exception):
    """One match ran past its budget (raised from the SIGALRM handler)."""


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
    these patterns reads one way only. An ambiguous one backtracks exponentially: a Goodbye
    mutated to `(?:bye\\W*)+` doubles per repeat (0.6-0.7 s at 20 repeats, 17-22 s at 25),
    and one mutated to `(?:\\w*bye)+` grows about eightfold per glued "byebyebyebye". The
    count ramps up one repeat at a time, which already stops a doubling pattern one repeat
    past the budget, 0.9-1.4 s into the test with the alarm or without it. The hard 0.5 s
    alarm on every match is a bound, not a speedup: no single match runs longer than 0.5 s,
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
    """What `ext.explain()` calls an action a robot is sent."""
    if action.type == ActionType.EXIT:
        return "the conversation ends"
    if action.type == ActionType.SLEEP:
        return "Moxie goes to sleep"
    assert action.type == ActionType.LAUNCH, action
    return f"Moxie starts the {action.module_id} activity"


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


DRAW = "Moxie starts the DRAW activity"
_WANTS_DRAW = {"contains": [{"lower": [{"var": "speech"}]}, "draw"]}

#: Imported programs whose `say` reaches an action tag in different ways: how the pack
#: review's sentence ends, and what the robot is sent for each thing the child says. Before
#: this, every one but the plain line and `random.pick` read with no "then" at all.
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
    "lower, which lowers the module too": (
        _says({"lower": ["<launch:DRAW>OK"]}),
        "; then Moxie starts the draw activity.", {"hi": ["Moxie starts the draw activity"]}),
    "concat": (
        _says({"concat": ["<sleep>", "Night ", "night"]}),
        "; then Moxie goes to sleep.", {"hi": ["Moxie goes to sleep"]}),
    "a tag split across concat parts": (
        _says({"concat": ["<ex", "it>See you!"]}),
        "; then the conversation ends.", {"hi": ["the conversation ends"]}),
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
        _says({"replace": ["<exot>Bye", "o", "i"]}),
        "; then the conversation ends.", {"hi": ["the conversation ends"]}),
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
        "; then sometimes Moxie starts an activity it works out.", {"draw": [DRAW]}),
    "a line that is only a tag": (
        _says("<sleep>"), "; then Moxie goes to sleep.", {"hi": ["Moxie goes to sleep"]}),
    "two lines, of which the robot is sent the last": (
        _imported({"do": [{"say": "<exit>Bye"}, {"say": "Hi there"}, {"handled": True}]}),
        "", {"hi": []}),
}


def _names(phrase, effect):
    """True when one of the review's phrases names `effect`, an action the robot was sent
    (a launch of a worked-out module names any launch)."""
    phrase = phrase[len("sometimes "):] if phrase.startswith("sometimes ") else phrase
    return phrase == effect or (phrase == "Moxie starts an activity it works out"
                                and effect.startswith("Moxie starts the "))


@pytest.mark.parametrize("shape", sorted(IMPORTED_SAYS))
def test_an_imported_say_names_what_its_tags_do_however_it_is_built(shape):
    """An extension's line goes through `parse_action_tags` with no other grant check, so
    the review's sentence is the only place a parent learns that it ends the chat, puts
    Moxie to sleep or starts an activity. Each program runs as an imported global with only
    the default grants: the sentence (in `explain()` and in the pack review) names what the
    robot is sent, "sometimes" when not every line it can say does it, and reads no tag."""
    program, then, heard = IMPORTED_SAYS[shape]
    (sentence,) = E.explain(program)
    assert sentence.endswith(then) if then else "; then" not in sentence, sentence
    assert "<" not in sentence and ">" not in sentence, sentence
    assert sentence in P.extension_warnings({"extension": program})
    named = sentence.partition("; then ")[2].rstrip(".").split(" and ") if then else []
    module = dict(CHAT_MODULE, globals=[{"name": "Probe", "pattern": r"\w",
                                         "extension": program}])
    for speech, want in heard.items():
        brain = Brain()
        reply = app_with(module, chat=brain).respond(
            Turn(robot=ext_robot(), speech=speech))
        assert brain.turns == [] and reply.text != QUESTION, (shape, speech)
        sent = [_effect_of(a) for a in reply.actions]
        assert sent == want, (shape, speech, reply)
        # What the robot was sent is named; what is named without "sometimes" always is.
        assert all(any(_names(n, e) for n in named) for e in sent), (sent, named)
        assert all(any(_names(n, e) for e in sent)
                   for n in named if not n.startswith("sometimes ")), (sent, named)


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


def test_a_say_built_to_multiply_is_read_in_bounded_time_and_still_names_its_tag():
    """Thirty `let` names that each double the lines of the last, and a `concat` of 32
    `if`s: each name is read once, in binding order, and past 256 lines every tag written
    in the `say` and its names counts as "sometimes". Read one by one, the first would be
    2^30 lines and the second 2^32."""
    chain = {"a0": {"if": [{"var": "speech"}, "<exit>Bye", "Hi"]}}
    for k in range(1, 30):
        chain[f"a{k}"] = {"if": [{"var": "speech"}, {"var": f"a{k - 1}"},
                                 {"concat": [{"var": f"a{k - 1}"}, "!"]}]}
    doubling = _says({"var": "a29"}, let=chain)
    wide = _says({"concat": [{"if": [{"var": "speech"}, "<sleep>z", "z"]}] * 32})
    for program in (doubling, wide):
        assert E.validate(program, grants=E.DEFAULT_GRANTS) == []
    try:
        with _hard_limit(5.0):
            read = E.explain(doubling) + E.explain(wide)
    except _Stalled:
        pytest.fail("explain() was still reading after 5 s")
    assert read[0].endswith("; then sometimes the conversation ends."), read[0]
    assert read[1].endswith("; then sometimes Moxie goes to sleep."), read[1]


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
    # and a shipped one, which both always agreed on
    next(c for c in _raw("memory_chat.json")["conversations"]
         if c["content_id"] == "default")["opener"],
]


@pytest.mark.parametrize("opener", PREVIEW_OPENERS, ids=[
    "filter", "if-filter", "comment-launch", "exit", "sleep", "empty-first", "shipped"])
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
