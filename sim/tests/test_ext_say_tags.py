"""An extension's spoken line acts only on the action tags written whole in its rule's own
text, and the pack review names every one of them. Together, the invariant: for every
program and every run-time input, the actions the robot is sent from a rule's line are
among the effects `explain()` names for that rule.

Five review rounds on `explain()` each found a new way to build a tag its reading ahead
missed (one split across parts, one around a part that comes out empty, one cased by
`upper`, one cut out by `get`, one read twice under a case op, a split into more pieces
than the value cap), and each sent EXIT, SLEEP or LAUNCH from an imported pack while the
parent's review named nothing. Enumerating them did not converge, so the guarantee now holds
by construction, failing closed: the host (`ext_host.apply_ext_effects`) takes any tag the
rule's text does not write whole (a string literal in a `say` or a `let` value,
`ext_host.literal_actions`) out of the line before the line is kept, counts it and tells the
parent once; `explain()` names every tag the rule writes whole, and its reading ahead only
decides the wording. Before this, an exit the review did not name could happen; after it,
it cannot. The robot's markup, which it speaks when it is given one, holds no tag of ours
and nothing the catalogue refuses, or the line goes without markup (`ext_host.robot_markup`).
A conversation's opener, which this PR makes speak on the robot path, is held to the same
rule (section E): it acts only on a tag written whole in the alternative said, unrendered,
and the pack review names each such tag in the opener's own row.

Hermetic: the real `ContentApp` (`helpers_ext.app_with`; the default grants, plus `random`,
`memory.read` and `markup` for the programs that declare them) over fake brains that count their own
calls, a tmp store for memory and the `ext_events` ring. The property tests run larger as a
script: `python3 sim/tests/test_ext_say_tags.py COUNT SEED MODE` from the checkout root,
MODE `literal` (tags only in the program's own text), `runtime` (what the child said, a
memory and an `input_vars` value hold tag pieces and whole tags too) or `opener` (COUNT
random opener templates).
"""
import importlib
import json
import os
import random
import re
import sys
import time
from collections import Counter

if __name__ == "__main__":                 # the script mode, from the checkout root
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "mqtt"))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from helpers_content import free_chat_pack
from helpers_ext import CHAT_MODULE, app_with, robot as ext_robot
from moxie_sdk import vocab
from moxie_sdk.actions import (_fields, _TAG_RE, drop_action_tags, lift_action_tags,
                               lift_every_action_tag, parse_action_tags, tag_names)
from moxie_sdk.content import content_app as CA
from moxie_sdk.content import ext as E
from moxie_sdk.content import ext_host as H
from moxie_sdk.content import load_modules, packs as P
from moxie_sdk.memory_store import MemoryStore
from moxie_sdk.store import JsonStore
from moxie_sdk.types import Action, ActionType, Turn
from test_leave_taking import (Brain, DRAW, GOODBYES, QUESTION, SHIPPED, SLEEPS, _hard_limit,
                               _imported, _named_in, _names, _pick, _raw, _says, _Stalled,
                               _WANTS_DRAW, shipped_app, robot as shipped_robot)

#: The module itself (the package re-exports `explain`, the function, under that name).
X = importlib.import_module("moxie_sdk.content.ext.explain")

#: The grants the property test runs under: the defaults, and what a generated program may
#: declare. The guard does not depend on grants; these only let the ops run, and `markup`
#: lets a program write the robot's markup, the channel the review's round 7 found open.
GRANTS = E.DEFAULT_GRANTS | {"random", "memory.read", "markup"}
#: Where a global named `Probe` keeps its memory (`ext_host.ext_namespace`).
NAMESPACE = "ext:global_probe"


def _words(action) -> str:
    """What `explain()` calls an action, with the module as `_plain` writes it: bare when it
    is an id shown exactly as written, quoted otherwise, braces and double quotes made
    spaces, a straight quote curly, format and control characters dropped, at most 80
    characters."""
    if action.type == ActionType.EXIT:
        return "the conversation ends"
    if action.type == ActionType.SLEEP:
        return "Moxie goes to sleep"
    assert action.type == ActionType.LAUNCH, action
    module = X._plain(action.module_id)
    return (f"Moxie starts the {module} activity"
            if module == action.module_id and X._MODULE_ID.fullmatch(module)
            else f"Moxie starts the '{module}' activity")


def _module(program, name="Probe"):
    """`program` as the only global, matching every non-empty utterance."""
    return dict(CHAT_MODULE, globals=[{"name": name, "pattern": r"[\s\S]", "extension": program}])


def _run(program, speech, *, grants=None, memory=None, input_vars=None, device="robot-1"):
    """One turn of `program` as an imported global: `(reply, app, brain)`."""
    brain = Brain()
    app = app_with(_module(program), chat=brain, ext_grants=grants or E.DEFAULT_GRANTS,
                   memory=memory if memory is not None else False,
                   clock=lambda: 1_700_000_000)
    reply = app.respond(Turn(robot=ext_robot(device), speech=speech,
                             input_vars=dict(input_vars or {})))
    return reply, app, brain


def _sent(reply) -> list:
    return [_words(a) for a in reply.actions]


def _refused(app) -> int:
    return sum(app._ext_refusals.values())


# --------------------------------------------------------------------------- #
# A. The runtime guard, fail closed
# --------------------------------------------------------------------------- #

_SPEECH = {"var": "speech"}
_IN = {"var": "input_vars.t"}
_MEM = {"var": "memory.m"}


def _memory(tmp_path, value, device="robot-1"):
    store = MemoryStore(JsonStore(str(tmp_path)))
    assert store.save(device, {NAMESPACE: {"m": value}})
    return store


#: Lines that carry a tag the rule's own text does not write whole, one per way of building
#: one: `(program, speech, input_vars, memory value, grants, what the review names)`. Each
#: parses to EXIT, SLEEP or a LAUNCH on the robot path; none may act.
BUILT = {
    "from pieces around what the child said": (
        _says({"concat": ["<ex", _SPEECH, ">Bye"]}), "it", {}, None, None, ""),
    "from pieces around a value the robot sent": (
        _says({"concat": ["<launch:", _IN, ">Go"]}), "hi", {"t": "DRAW"}, None, None, ""),
    "from pieces around a note from earlier this turn": (
        _says({"concat": ["<ex", {"var": "scratch.x"}, "it>"]}), "hi", {}, None, None, ""),
    "the whole tag in what the child said": (
        _says(_SPEECH), "<exit>Bye", {}, None, None, ""),
    "the whole tag in a value the robot sent": (
        _says({"concat": [_IN, "!"]}), "hi", {"t": "<launch:DRAW>Go"}, None, None, ""),
    "the whole tag in a memory": (
        _says(_MEM, caps=("handled", "memory.read", "say")), "hi", {}, "<sleep>Night",
        E.DEFAULT_GRANTS | {"memory.read"}, ""),
    "a launch lowered (fields compare exactly)": (
        _says({"lower": ["<launch:DRAW>OK"]}), "hi", {}, None, None,
        f"; then sometimes {DRAW}."),
    "a launch uppered": (
        _says({"upper": ["<launch:draw>ok"]}), "hi", {}, None, None,
        "; then sometimes Moxie starts the draw activity."),
    "a dotless i that upper makes an i": (
        _says({"upper": [_pick("<exıt>Bye ", "Hi")]}), "ab", {}, None, None, ""),
    "a long s that upper makes an S": (
        _says({"upper": ["<ſleep>Night"]}), "hi", {}, None, None, ""),
    "cut out by get": (
        _says({"concat": [{"get": [{"lit": ["<ex", "x"]}, {"%": [{"len": [_SPEECH]}, 2]}]},
                          "it>Bye"]}), "ab", {}, None, None, ""),
    "cut out by slice": (
        _says({"concat": [{"slice": ["<exot", 0, 3]}, "it>Bye"]}), "hi", {}, None, None, ""),
    "cut out by split and join": (
        _says({"join": [{"split": ["<ex,it>Bye", ","]}, ""]}), "hi", {}, None, None, ""),
    "rewritten by replace": (
        _says({"replace": ["<exot>Bye", "o", "i"]}), "hi", {}, None, None, ""),
    "rewritten by reverse": (
        _says({"reverse": ["eyB>tixe<"]}), "hi", {}, None, None, ""),
    "a launch with its module worked out": (
        _says({"concat": ["<launch:", {"upper": [{"trim": [_SPEECH]}]}, ">Go"]}),
        "draw", {}, None, None, ""),
    # Written whole in the rule, but not in a `say` or a `let`: the line carries the same
    # tag from what the child said, and the host must not count these statements' text.
    "written only in the rule's when": (
        _imported({"when": {"!=": [_SPEECH, "<exit>"]},
                   "do": [{"say": _SPEECH}, {"handled": True}]}),
        "<exit>Bye", {}, None, None, ""),
    "written only in a scratch value": (
        _imported({"do": [{"scratch": {"key": "x", "value": "<exit>Bye"}}, {"say": _SPEECH},
                          {"handled": True}]}),
        "<exit>Bye", {}, None, None, ""),
    "written only in a remember value": (
        _imported({"do": [{"remember": {"key": "k", "value": "<exit>Bye"}}, {"say": _SPEECH},
                          {"handled": True}]}, caps=("handled", "memory.write", "say")),
        "<exit>Bye", {}, "", E.DEFAULT_GRANTS | {"memory.write"}, ""),
    "written only in a log note": (
        _imported({"do": [{"note": "<exit>Bye"}, {"say": _SPEECH}, {"handled": True}]}),
        "<exit>Bye", {}, None, None, ""),
    "written only in the line's markup": (
        _imported({"do": [{"say": _SPEECH, "markup": "<exit>Bye"}, {"handled": True}]},
                  caps=("handled", "markup", "say")),
        "<exit>Bye", {}, None, E.DEFAULT_GRANTS | {"markup"}, ""),
}


@pytest.mark.parametrize("shape", sorted(BUILT))
def test_a_line_acts_only_on_a_tag_written_whole_in_its_rules_own_text(shape, tmp_path):
    """Each built tag reaches the host as a well-formed `<exit>`, `<sleep>` or `<launch:…>`
    in the line (so before this it acted); the host takes it out: the robot is sent
    nothing, the child hears the line without it, the model is not asked, the tag is
    counted, and the review names nothing but what the rule writes whole."""
    program, speech, input_vars, memory, grants, then = BUILT[shape]
    assert E.validate(program, grants=grants or E.DEFAULT_GRANTS) == [], shape
    store = _memory(tmp_path, memory) if memory is not None else None
    reply, app, brain = _run(program, speech, grants=grants, memory=store,
                             input_vars=input_vars)
    assert brain.turns == [] and reply.text != QUESTION, (shape, reply)
    assert reply.actions == [], (shape, reply)
    assert tag_names(reply.text) == [] and "<" not in reply.text, (shape, reply.text)
    assert _refused(app) >= 1 and not app._ext_breaches, shape
    (sentence,) = E.explain(program)
    assert sentence.endswith(then) if then else "; then" not in sentence, sentence
    assert sentence in P.extension_warnings({"extension": program})


def test_a_memory_the_program_wrote_on_an_earlier_turn_cannot_carry_a_tag_into_its_line(tmp_path):
    """The tag is written in a `remember` value, not in a `say` or a `let`: on the next turn
    the line reads it from memory and the host takes it out. The parent's review of the
    second rule names nothing, and nothing happens."""
    program = {"ext_format": 1, "capabilities": ["handled", "memory.read", "memory.write", "say"],
               "on": "global", "rules": [
                   {"when": {"==": [_SPEECH, "one"]},
                    "do": [{"remember": {"key": "line", "value": "<exit>Bye"}},
                           {"say": "Noted."}, {"handled": True}]},
                   {"do": [{"say": {"var": "memory.line"}}, {"handled": True}]}]}
    grants = E.DEFAULT_GRANTS | {"memory.read", "memory.write"}
    assert E.validate(program, grants=grants) == []
    store = MemoryStore(JsonStore(str(tmp_path)))
    app = app_with(_module(program), chat=Brain(), ext_grants=grants, memory=store)
    first = app.respond(Turn(robot=ext_robot(), speech="one"))
    assert first.text == "Noted." and first.actions == []
    assert "<exit>Bye" in json.dumps(store.load("robot-1").get(NAMESPACE))
    second = app.respond(Turn(robot=ext_robot(), speech="two"))
    assert second.text == "Bye" and second.actions == [], second
    assert app._ext_refusals == {("robot-1", "global:Probe"): 1}
    assert "; then" not in E.explain(program)[1]


#: Lines whose tag is written whole in the rule: `(program, speech, grants, sent, then)`.
LITERAL = {
    "in a let": (
        _says({"var": "line"}, let={"line": "<launch:DRAW>Go"}), "hi", None, [DRAW],
        f"; then {DRAW}."),
    "in an if branch": (
        _says({"if": [_WANTS_DRAW, "<launch:DRAW>Go", "Hi"]}), "draw", None, [DRAW],
        f"; then sometimes {DRAW}."),
    "in a random.pick": (
        _says({"random.pick": [{"lit": ["<launch:DRAW>Go", "<launch:DRAW>Now"]}]},
              caps=("handled", "random", "say")), "hi", E.DEFAULT_GRANTS | {"random"},
        [DRAW], f"; then {DRAW}."),
    "in a lit list a get reads": (
        _says({"get": [{"lit": ["<launch:DRAW>Go", "Hi"]}, 0]}), "hi", None, [DRAW],
        f"; then {DRAW}."),
    "as a lit map's key, read by keys": (
        _says({"get": [{"keys": [{"lit": {"<launch:DRAW>Go": 1}}]}, 0]}), "hi", None, [DRAW],
        f"; then {DRAW}."),
    "beside what the child said": (
        _says({"concat": ["<launch:DRAW>", _SPEECH]}), "hi", None, [DRAW], f"; then {DRAW}."),
    "under upper, since the name is case-insensitive": (
        _says({"upper": ["<exit>bye"]}), "hi", None, ["the conversation ends"],
        "; then the conversation ends."),
    "with a content id": (
        _says("<launch:DRAW:story>Go"), "hi", None, [DRAW], f"; then {DRAW}."),
    "in a let the line never reads, completed by what the child said": (
        _says({"concat": ["<ex", _SPEECH, ">"]}, let={"t": "<exit>Bye"}), "it", None,
        ["the conversation ends"], "; then sometimes the conversation ends."),
    "twice, once lowered: the written one acts": (
        _says({"concat": [_pick("<launch:DRAW>", "Hi"), {"lower": [_pick("<launch:DRAW>", "Hi")]}]}),
        "ab", None, [DRAW], f"; then sometimes {DRAW}."),
}


@pytest.mark.parametrize("shape", sorted(LITERAL))
def test_a_tag_written_whole_in_the_rule_still_acts_and_is_named(shape):
    """Shipped-style programs are untouched: a literal `<launch:DRAW>` wherever it is
    written launches, is named, and nothing is counted as taken out (but the lowered copy
    in the last shape, which the rule does not write)."""
    program, speech, grants, sent, then = LITERAL[shape]
    assert E.validate(program, grants=grants or E.DEFAULT_GRANTS) == [], shape
    reply, app, brain = _run(program, speech, grants=grants)
    assert brain.turns == [] and _sent(reply) == sent, (shape, reply)
    assert tag_names(reply.text) == [], reply.text
    assert (_refused(app) > 0) == shape.startswith("twice"), (shape, _refused(app))
    (sentence,) = E.explain(program)
    assert sentence.endswith(then), sentence
    for e in sent:
        assert any(_names(n, e) for n in _named_in(sentence)), (e, sentence)


def test_taking_a_tag_out_cannot_let_the_pieces_around_it_meet():
    """Taking `<sleep>` out of `<ex<sleep>it>` leaves `<exit>`, which the robot path would
    then read: `drop_action_tags` repeats until the line parses to allowed tags only. A kept
    tag, a malformed one and a tag that is not ours stay exactly as written."""
    none, every = (lambda a: False), (lambda a: True)
    sleep, exit_ = Action(type=ActionType.SLEEP), Action(type=ActionType.EXIT)
    assert drop_action_tags("<ex<sleep>it>Bye", none) == ("Bye", [sleep, exit_])
    assert drop_action_tags("<exit<sleep>>Bye", none) == ("Bye", [sleep, exit_])
    assert drop_action_tags("<exit>Bye", every) == ("<exit>Bye", [])
    assert drop_action_tags("<exit:now>x <mark name='a'/> <opener> <launch:A:B:C>", none) == (
        "<exit:now>x <mark name='a'/> <opener> <launch:A:B:C>", [])
    assert drop_action_tags("<launch:DR<sleep>AW>Go", lambda a: a.type is ActionType.LAUNCH) == (
        "<launch:DRAW>Go", [sleep])
    assert drop_action_tags("", none) == ("", [])
    # Through the host: the sleep the child said sits inside the exit's pieces.
    program = _says({"concat": ["<ex", _SPEECH, "it>Bye"]})
    reply, app, _ = _run(program, "<sleep>")
    assert reply.actions == [] and reply.text == "Bye" and _refused(app) == 2
    # When the rule writes an exit whole elsewhere, the exit that forms is one the review
    # names, so it acts.
    program = _says({"concat": ["<ex", _SPEECH, "it>Bye"]}, let={"t": "<exit>"})
    reply, app, _ = _run(program, "<sleep>")
    assert [a.type for a in reply.actions] == [ActionType.EXIT] and reply.text == "Bye"
    assert _refused(app) == 1
    assert "sometimes the conversation ends" in _named_in(E.explain(program)[0])


def test_a_tag_that_forms_only_once_the_kept_tags_are_lifted_is_never_spoken():
    """The robot's parse lifts every tag of ours in one pass, so a line like `<ex<sleep>it>`
    whose sleep the rule writes whole would keep its sleep, and the child would then hear
    `<exit>` said aloud (never acted on: one pass). The host cuts such a tag out with the
    pieces it is made of: the kept tag still acts, the child hears no tag, and the cut tag
    is counted only when the rule does not write it whole. Found by a second generator in
    round 6, where the suite's own shapes never put a kept tag inside another's pieces."""
    sleep_only = (lambda a: a.type is ActionType.SLEEP)
    sleep, exit_ = Action(type=ActionType.SLEEP), Action(type=ActionType.EXIT)
    draw = Action(type=ActionType.LAUNCH, module_id="DRAW")
    assert drop_action_tags("<ex<sleep>it>Bye", sleep_only) == ("<sleep>Bye", [exit_])
    assert drop_action_tags("<ex<sleep>it>Bye", lambda a: True) == ("<sleep>Bye", [])
    assert drop_action_tags("<la<exit>unch:DRAW>Go", lambda a: a.type is ActionType.EXIT) == (
        "<exit>Go", [draw])
    assert drop_action_tags("<ex<sleep>it:now>Bye", sleep_only) == ("<sleep>Bye", [])
    assert drop_action_tags("<e<ex<sleep>it>xit>Bye", sleep_only) == ("<sleep>Bye", [exit_, exit_])
    assert drop_action_tags("<sleep> <ex it>", sleep_only) == ("<sleep> <ex it>", [])
    for text, kept in (("<ex<sleep>it>Bye", sleep_only), ("<e<ex<sleep>it>xit>Bye", sleep_only),
                       ("<la<exit>unch:DRAW>Go", lambda a: a.type is ActionType.EXIT)):
        spoken, acts = parse_action_tags(drop_action_tags(text, kept)[0])
        assert tag_names(spoken) == [] and len(acts) == 1, (text, spoken, acts)
    # Through the host: the rule writes its sleep whole, so Moxie sleeps; the exit that
    # would have been spoken is cut and counted (the rule does not write it).
    reply, app, _ = _run(_says("<ex<sleep>it>Bye"), "hi")
    assert reply.text == "Bye" and [a.type for a in reply.actions] == [ActionType.SLEEP]
    assert tag_names(reply.text) == [] and _refused(app) == 1
    assert E.explain(_says("<ex<sleep>it>Bye"))[0].endswith("; then Moxie goes to sleep.")
    # When the rule writes an exit whole too, the cut exit is one its review names (as
    # "sometimes"), so nothing is counted; the robot path never acted on it either way.
    program = _says("<ex<sleep>it>Bye", let={"t": "<exit>"})
    reply, app, _ = _run(program, "hi")
    assert reply.text == "Bye" and [a.type for a in reply.actions] == [ActionType.SLEEP]
    assert _refused(app) == 0
    assert _named_in(E.explain(program)[0]) == ["Moxie goes to sleep", "sometimes the conversation ends"]


def test_no_other_path_lets_a_built_tag_act():
    """A `scratch` value is never spoken, an earlier `say`'s written exit is replaced by the
    later line (which carries a built tag, taken out), and a conversation's `turn.before`
    program and the `perceive` path go through the same host. Markup, the channel beside
    the line, is `test_the_robots_markup_holds_no_tag_of_ours_and_nothing_the_catalogue_refuses`."""
    tagged = {"concat": ["<ex", _SPEECH, "it>Hi"]}
    program = _imported({"do": [{"scratch": {"key": "x", "value": tagged}}, {"say": "Hi"},
                                {"handled": True}]})
    reply, app, _ = _run(program, "it")
    assert reply.text == "Hi" and reply.actions == [] and _refused(app) == 0
    program = _imported({"do": [{"say": "<exit>Bye"}, {"say": {"concat": ["<sl", _SPEECH, ">"]}},
                                {"handled": True}]})
    reply, app, _ = _run(program, "eep")
    assert reply.text == "" and reply.actions == [] and _refused(app) == 1
    before = {"ext_format": 1, "capabilities": ["handled", "say"], "on": "turn.before",
              "rules": [{"do": [{"say": {"concat": ["<ex", _SPEECH, ">Bye"]}}, {"handled": True}]}]}
    module = {"conversations": [dict(CHAT_MODULE["conversations"][0], extension=before)]}
    brain = Brain()
    app = app_with(module, chat=brain)
    reply = app.respond(Turn(robot=ext_robot(), speech="it"))
    assert brain.turns == [] and reply.text == "Bye" and reply.actions == []
    assert app._ext_refusals == {("robot-1", "conversation:CHAT/default"): 1}
    seen = app.perceive(Turn(robot=ext_robot(), speech="eb-found-face"))
    assert seen is not None and seen.actions == []
    assert app._ext_refusals == {("robot-1", "conversation:CHAT/default"): 1}, "no tag formed"
    noticed = {"ext_format": 1, "capabilities": ["handled", "say"], "on": "turn.before",
               "rules": [{"do": [{"say": {"concat": ["<exi", {"slice": [_SPEECH, 6, 7]}, ">"]}},
                                 {"handled": True}]}]}
    module = {"conversations": [dict(CHAT_MODULE["conversations"][0], extension=noticed)]}
    app = app_with(module, chat=Brain())
    seen = app.perceive(Turn(robot=ext_robot(), speech="eb-lost-target"))
    assert seen is not None and seen.text == "" and seen.actions == []
    assert app._ext_refusals == {("robot-1", "conversation:CHAT/default"): 1}


#: The grants a program with markup runs under, and catalogue tags the host keeps or drops.
MARKUP = E.DEFAULT_GRANTS | {"markup"}
GOOD_MARK = '<mark name="cmd:playback-mood,data:{+mood+:0,+intensity+:1}"/>'
BAD_MARK = '<mark name="cmd:zzz"/>'
GOOD_USEL = '<usel genre="question">'
BAD_USEL = '<usel genre="nope">'

#: Markup a program may write, and what the host lets reach the robot of it: `(markup, what
#: the robot is given before its markup floor, the tags the catalogue dropped plus one for a
#: markup dropped whole)`. The last three shapes are the review's (round 7): a tag the
#: catalogue drops stood between the pieces of a tag of ours, and reached the robot as
#: `<exit>Hi`, `<sleep>Hi` and `<launch:DRAW>Hi`; the one after is the catalogue's own
#: counterpart (a mark the catalogue would refuse forms once the robot has lifted the exit).
MARKUPS = {
    "a plain tag of ours is lifted as the robot would, the rest kept": (
        f"<exit>{GOOD_USEL}Hi", f"{GOOD_USEL}Hi", 0),
    "a malformed tag of ours is lifted too": (f"<exit:now>{GOOD_USEL}Hi", f"{GOOD_USEL}Hi", 0),
    "a tag the catalogue refuses is dropped, a valid one kept": (
        f"{BAD_MARK}{GOOD_MARK}Hi", f"{GOOD_MARK}Hi", 1),
    "a tag closed by a valid tag's own pieces is not ours, and stays": (
        f"<ex{GOOD_USEL}it>Hi", f"<ex{GOOD_USEL}it>Hi", 0),
    "a tag that forms once the lifted tag's pieces meet": ("<ex<sleep>it>Hi", "", 1),
    "a tag three lifts deep": ("<e<ex<sleep>it>xit>Hi", "", 1),
    "a mark the catalogue drops stood between an exit's pieces": (
        f"<ex<ex{BAD_MARK}it>it>Hi", "", 2),
    "a usel the catalogue drops stood between a sleep's pieces": (
        f"<sl<sl{BAD_USEL}eep>eep>Hi", "", 2),
    "a mark the catalogue drops stood between a launch's pieces": (
        f"<la<launch:DR{BAD_MARK}AW>unch:DRAW>Hi", "", 2),
    "a mark the catalogue would refuse forms once the robot lifts the exit": (
        '<m<ex<sleep>it>ark name="cmd:zzz"/>Hi', "", 1),
    # Found by this round's generator, and open on dev: the gate is one pass, so the pieces
    # around a tag it drops can form a tag it never saw; and a `>` inside a tag's quotes
    # cuts the tag short for the gate while the robot reads it whole.
    "a usel the catalogue drops stood between a spurt's pieces": (
        f"<spu{BAD_USEL}rt spurt_id=\"nope\"/>Hi", "", 2),
    "a tag of ours lifted leaves a usel the catalogue refuses, and it goes": (
        f"<us<exit>el genre=\"nope\">Hi", "Hi", 1),
    "a spurt cut short by a > inside its quotes is malformed, and goes": (
        '<spurt spurt_id="n>pe"/>Hi', 'pe"/>Hi', 1),
    "a mark cut short by a > inside its data is malformed, and goes": (
        '<mark name="cmd:playback-mood,data:{+mood+:0>,+intensity+:1}"/>Hi', ',+intensity+:1}"/>Hi', 1),
    # Round 9, the review's six (round 8's check ran before the robot path tidied the
    # markup, and tag by tag only). Tidying takes out the space before a comma, which joins
    # a mark the catalogue refuses: its verb, its sound, its mood.
    "tidying would join a mark with a verb the catalogue refuses": (
        '<mark name="cmd:zzz ,data:{}"/>Hi', "", 1),
    "tidying would join a mark with a sound the catalogue refuses": (
        '<mark name="cmd:playaudio ,data:{+SoundToPlay+:+nope+}"/>Hi', "", 1),
    "tidying would join a mark with a mood the catalogue refuses": (
        '<mark name="cmd:playback-mood ,data:{+mood+:+nope+,+intensity+:1}"/>Hi', "", 1),
    # A quoted `>` after an earlier unbalanced quote: tag by tag the quotes even out, while
    # the catalogue's check over the whole text reads the `>` inside the value.
    "a spurt id holding a quoted > after an unbalanced quote": (
        '<spurt x" spurt_id="n>pe"/>Hi', "", 1),
    "a mark whose quoted data holds a >": (
        '<mark name="cmd:playback-mood,data:{"a":">"}"/>Hi', "", 1),
    "a usel genre holding a quoted > after an unbalanced quote": (
        '<usel x" genre="a>b">Hi</usel>', "", 1),
    # What keeps the whole-text check off the shapes it reads in more than linear time: a
    # catalogue tag left open (it reads on from such an opening, and passed this one), and
    # a catalogue tag holding another `<` (it passed this one too).
    "a catalogue tag left open goes, with the markup": ('Hi <usel genre="nope"', "", 1),
    "a catalogue tag holding another < goes": ('<usel<usel genre="question">Hi', "Hi", 1),
}


@pytest.mark.parametrize("shape", sorted(MARKUPS))
def test_the_robots_markup_holds_no_tag_of_ours_and_nothing_the_catalogue_refuses(shape):
    """The robot speaks its markup when it is given one (`_reply_from_volley` sends
    `parse_action_tags(markup)[0]`: our tags lifted once, as from a line, then tidied), and
    markup acts on nothing, so `robot_markup` lets markup reach the robot only with no tag
    of ours and nothing outside the catalogue: every tag of ours is lifted as the robot's
    own parse lifts them, the catalogue drops what it refuses, the rest is tidied as the
    robot path tidies it, and if a tag of ours, a tag the catalogue would drop, a tag left
    open or anything the catalogue's own whole-text check refuses is then in what is left,
    the markup is dropped whole and the runtime's markup floor speaks the line. Before
    round 8 the tags were taken out to a fixpoint and the catalogue checked after, so the
    pieces around a dropped tag met and the robot was given `<exit>Hi`; before round 9 the
    last check ran before the tidying and tag by tag only, so the robot was given marks and
    a spurt and a usel the catalogue refuses. Checked on the function and through the real
    app, for a `say`'s markup and for a `markup` statement after the say; a statement
    before the say is replaced by the say's own output, so it could never show a leak.
    Never counted as a refusal and never a breach. The robot path annotates a markup that
    holds no tag at all (`annotate` leaves one with a tag alone), so a surviving markup
    reaches the robot as `annotate` leaves it: the catalogue's own marks around the same
    text."""
    markup, clean, dropped = MARKUPS[shape]
    assert H.robot_markup(markup) == (clean, dropped), shape
    assert tag_names(clean) == [] and vocab.validate_markup(clean) == [], shape
    assert lift_action_tags(clean) == clean, "the robot's own lift finds nothing left"
    assert parse_action_tags(clean) == (clean, []), "the robot path sends what was checked"
    assert H.ext_markup(clean) == (clean, 0), "the gate would drop nothing more"
    for program in (
            _imported({"do": [{"say": "Hi", "markup": markup}, {"handled": True}]},
                      caps=("handled", "markup", "say")),
            _imported({"do": [{"say": "Hi"}, {"markup": markup}, {"handled": True}]},
                      caps=("handled", "markup", "say"))):
        assert E.validate(program, grants=MARKUP) == []
        reply, app, brain = _run(program, "hi", grants=MARKUP)
        assert brain.turns == [] and reply.text == "Hi" and reply.actions == [], (shape, reply)
        assert tag_names(reply.markup or "") == [], (shape, reply.markup)
        assert vocab.validate_markup(reply.markup or "") == [], (shape, reply.markup)
        assert H.ext_markup(reply.markup or "") == (reply.markup or "", 0), (shape, reply.markup)
        assert (reply.markup is None) == (clean == ""), (shape, reply.markup)
        if clean:
            expected = CA.annotate(clean) if CA._automarkup_enabled() else clean
            assert reply.markup == expected, (shape, reply.markup)
        assert _refused(app) == 0 and not app._ext_breaches, shape
    before = _imported({"do": [{"markup": markup}, {"say": "Hi"}, {"handled": True}]},
                       caps=("handled", "markup", "say"))
    assert _run(before, "hi", grants=MARKUP)[0].markup is None, "the say replaces it"


#: Markups at the cap that a pass of `robot_markup` once read in more than linear time.
BOUNDED_MARKUPS = {
    # Round 8: a nest of our tag pieces around a malformed tag, which the fixpoint pass
    # read once per level (0.6-1.0 s per nest).
    "a nest of tag pieces": "<ex" * 1363 + "<exit:now>" + "it>" * 1363,
    # Round 9, the review's: `<mark` openings with no `>` after them, which `_EXT_TAG` read
    # to the end once per opening, in the gate and again in the last pass.
    "a run of <mark": ("<mark" * 1639)[:8192],
    # Round 9: openings the catalogue's whole-text check reads on from, to the end (from
    # each `genre="` again), so it is dropped as left open before that check runs ...
    'a run of <usel genre="': ('<usel genre="' * 631)[:8192],
    # ... and one tag holding thousands of openings, which the catalogue check reads from
    # each, so the gate refuses it for holding a `<` before that check runs.
    "one tag of <spurt openings": ("<spurt" * 1366)[:8191] + ">",
}


@pytest.mark.parametrize("shape", sorted(BOUNDED_MARKUPS))
def test_four_markups_at_the_cap_are_cleared_in_time_linear_in_their_text(shape):
    """A turn carries at most four spoken lines and markup statements together
    (`MAX_ACTIONS`), each with up to 8,192 characters of markup, cleared on every turn
    with the GIL held; here five turns of four, 7-17 ms in all on the build host. Before
    round 8 a markup went through `drop_action_tags` to a fixpoint, quadratic in the markup:
    a turn with four nests took 2.1-3.7 s. Before round 9 the gate's tag search read from
    each opening with no `>` after it to the end of the markup, twice (a turn with four
    `<mark` runs took 0.37-0.52 s), and the catalogue's whole-text check, which round 9
    adds, takes 0.4-0.7 s on one `<usel genre="` run unless an opening left open drops the
    markup first, measured. With any one of round 9's bounds undone the five turns take
    0.94-6.8 s. The alarm turns a super-linear pass red at 5 s rather than later; the 0.5 s
    bound is what fails one on a quiet host."""
    markup = BOUNDED_MARKUPS[shape]
    assert len(markup) <= E.MAX_MARKUP_CHARS
    program = {"ext_format": 1, "capabilities": ["handled", "markup", "say"], "on": "global",
               "rules": [{"do": [{"say": "Hi", "markup": markup}] * 4 + [{"handled": True}]}]}
    assert E.validate(program, grants=MARKUP) == []
    app = app_with(_module(program), chat=Brain(), ext_grants=MARKUP, clock=lambda: 1_700_000_000)
    started = time.perf_counter()
    try:
        with _hard_limit(5.0):
            replies = [app.respond(Turn(robot=ext_robot(), speech="hi")) for _ in range(5)]
    except _Stalled:
        pytest.fail(f"still clearing four 8 KB markups ({shape}) after 5 s")
    took = time.perf_counter() - started
    for reply in replies:
        assert reply.text == "Hi" and reply.actions == [] and reply.markup is None, reply
    assert _refused(app) == 0 and not app._ext_breaches
    assert took < 0.5, f"five turns of four 8 KB markups ({shape}) took {took:.2f} s"
    assert H.robot_markup(markup) == ("", 1)
    assert H.robot_markup("<ex" * 1364 + "<sleep>" + "it>" * 1364) == ("", 1)


def test_a_taken_out_tag_is_told_to_the_parent_once_and_is_not_a_breach(tmp_path, capsys):
    """Reported through the breach path (one `ext_events` row per device, extension and
    reason, with a plain sentence) and counted, but never a breach: three in a row do not
    quarantine, the fourth turn still runs the program, and a breach of the same program
    gets its own row and its own count. The log line names the tag's kind only, never its
    text, which here is what the child said."""
    store = MemoryStore(JsonStore(str(tmp_path)))
    program = _says({"concat": ["<launch:", _SPEECH, ">Go"]})
    brain = Brain()
    app = app_with(_module(program), chat=brain, memory=store, clock=lambda: 1_700_000_000)
    for _ in range(4):
        reply = app.respond(Turn(robot=ext_robot(), speech="zebra"))
        assert reply.text == "Go" and reply.actions == []
    assert brain.turns == [], "the fourth turn still ran the program: no quarantine"
    assert app._ext_refusals == {("robot-1", "global:Probe"): 4}
    assert app._ext_breaches == {}
    rows = store.store.read("robot-1", CA.EXT_EVENTS_COLLECTION, [])
    assert rows == [{"at": 1_700_000_000, "extension": "global:Probe", "hook": "global",
                     "reason": H.REFUSED_TAG_REASON, "sentence": H.REFUSED_TAG_WORDS,
                     "quarantined": False}], rows
    out = capsys.readouterr().out
    assert out.count("[ext] global:Probe") == 1 and "launch" in out and "zebra" not in out
    # Another robot: its own row.
    app.respond(Turn(robot=ext_robot("robot-2"), speech="zebra"))
    assert len(store.store.read("robot-2", CA.EXT_EVENTS_COLLECTION, [])) == 1
    assert len(store.store.read("robot-1", CA.EXT_EVENTS_COLLECTION, [])) == 1
    # A breach of the same program (a value too big) is a different reason: a second row,
    # and it counts towards quarantine where the refusals did not.
    app.module = load_modules(_module(_says({"repeat": [{"repeat": ["x" * 1000, 16]}, 16]})))
    reply = app.respond(Turn(robot=ext_robot(), speech="zebra"))
    assert reply.text == QUESTION, "the breach fell through to the brain"
    rows = store.store.read("robot-1", CA.EXT_EVENTS_COLLECTION, [])
    assert [r["reason"] for r in rows] == [H.REFUSED_TAG_REASON, "value"], rows
    assert app._ext_breaches == {("robot-1", "global:Probe"): 1}


def test_the_tags_a_program_writes_are_read_once_per_program(monkeypatch):
    """Read once and kept by the program's digest, so a turn costs a lookup; an edited
    program (a new digest) is read afresh, so its new tag acts and the old one does not."""
    calls = []
    real = CA.literal_actions
    monkeypatch.setattr(CA, "literal_actions", lambda block: calls.append(1) or real(block))
    program = _says({"var": "line"}, let={"line": "<exit>Bye"})
    brain = Brain()
    app = app_with(_module(program), chat=brain)
    for _ in range(5):
        reply = app.respond(Turn(robot=ext_robot(), speech="hi"))
        assert [a.type for a in reply.actions] == [ActionType.EXIT]
    assert len(calls) == 1
    edited = _says({"var": "line"}, let={"line": "<sleep>Bye"})
    app.module = load_modules(_module(edited))
    reply = app.respond(Turn(robot=ext_robot(), speech="hi"))
    assert [a.type for a in reply.actions] == [ActionType.SLEEP] and len(calls) == 2
    app.module = load_modules(_module(program))
    app.respond(Turn(robot=ext_robot(), speech="hi"))
    assert len(calls) == 2, "the first program's set was still kept"
    assert H.literal_actions(program) == (frozenset({H._action_key(Action(type=ActionType.EXIT))}),)
    assert H.literal_actions({"rules": "junk"}) == () and H.literal_actions(None) == ()


# --------------------------------------------------------------------------- #
# B. The review names every tag that can act; the reading ahead decides the wording
# --------------------------------------------------------------------------- #

#: Literals built to stall the parse that reads a program's text: a run of spaces after
#: `<exit:` or `<exit` with no `>` after it, and one closed by `x>`, 200,000 characters each.
STALLERS = {
    "<exit: and 200,000 spaces": "<exit:" + " " * 200_000,
    "<exit and 200,000 spaces, no colon": "<exit" + " " * 200_000,
    "<launch: and 200,000 spaces, then x>": "<launch:" + " " * 200_000 + "x>",
}


@pytest.mark.parametrize("shape", sorted(STALLERS))
def test_the_tags_a_program_writes_are_read_in_time_linear_in_its_text(shape):
    """The literal set is read on the first turn any rule of a program matches, over every
    string the rule writes, taken or not (here in an `if` branch never taken), with the
    GIL held, and a pack may carry a megabyte of them: so the read must be linear in the
    program's text. `_TAG_RE`'s fields are greedy and run up to the `>` itself, so no two
    neighbouring repeats can take the same character. With lazy fields followed by
    `\\s*>` each of these first turns took 0.35 s at 16,000 spaces through the real app,
    about four times longer per doubling (2.7 s at 64,000; still running after 8 s at a
    megabyte), and every thread of the supervisor waited; now each takes milliseconds.
    The alarm turns a quadratic read red at 5 s instead of minutes later."""
    program = _says({"if": [False, STALLERS[shape], "Hi"]})
    assert E.validate(program, grants=E.DEFAULT_GRANTS) == []
    app = app_with(_module(program), chat=Brain(), clock=lambda: 1_700_000_000)
    started = time.perf_counter()
    try:
        with _hard_limit(5.0):
            reply = app.respond(Turn(robot=ext_robot(), speech="hi"))
    except _Stalled:
        pytest.fail(f"the first turn was still reading {shape} after 5 s")
    took = time.perf_counter() - started
    assert reply.text == "Hi" and reply.actions == [] and _refused(app) == 0, reply
    assert took < 0.5, f"the first turn took {took:.2f} s on {shape}"
    # The review reads the same literal (linearly since round 4) and names what it parses
    # to: nothing for the two unclosed ones, a launch of the module `x` for the closed one.
    assert _named_in(E.explain(program)[0]) == [
        f"sometimes {_words(a)}" for a in parse_action_tags(STALLERS[shape])[1]]


def test_a_literal_at_the_pack_cap_is_read_in_bounded_time():
    """The largest string a pack can carry, `<exit:` and a megabyte of spaces: the host's
    read of the program and the robot's own parse of the string both finish well inside a
    second (before, neither had finished after 8 s)."""
    at_cap = "<exit:" + " " * 2 ** 20
    started = time.perf_counter()
    try:
        with _hard_limit(5.0):
            assert H.literal_actions(_says({"if": [False, at_cap, "Hi"]})) == (frozenset(),)
            assert parse_action_tags(at_cap) == ("<exit:", [])
    except _Stalled:
        pytest.fail("still reading a megabyte of spaces after 5 s")
    took = time.perf_counter() - started
    assert took < 1.0, f"a megabyte of spaces took {took:.2f} s"


#: Pieces of the tag grammar, for random lines: its characters, Unicode spaces, ı, ſ, a
#: private-use character, braces, quotes and a 90-character module.
_GRAMMAR_POOL = ["<", ">", ":", " ", "\t", " ", "exit", "EXIT", "eXit", "sleep", "ſleep",
                 "launch", "_if_confirmed", "DRAW", "x", "2", "", "<exit>", "<launch:",
                 "ı", "{", '"', "\n", "Draw now", "A" * 90]


def test_the_tag_parse_reads_what_its_lazy_form_read():
    """`_TAG_RE` had lazy fields followed by `\\s*>` until round 7. The greedy form matches
    the same span with the same name and the same fields on every line (the fields run up
    to the `>` itself, and `_fields` strips the spaces before it), so every caller reads
    what it read: pinned on the drift corpus, the whitespace-heavy forms and 20,000 random
    lines over the grammar's characters."""
    lazy = re.compile(r"<\s*([A-Za-z_][A-Za-z0-9_]*)\s*((?::[^<>]*?)?)\s*>")
    rng = random.Random(7)
    lines = ["<exit >", "< exit>", "<exit: >", "<launch:DRAW >", "<launch: DRAW : story >",
             "<launch:DRAW: >", "<launch: : >", "<exit:\n>", "<exit\t:now\t>", "<exit:>",
             "<exit:  x", "<exit  ", "<launch:  x>  <exit>", "<<exit>>", "<exit:<sleep>>",
             "<launch:DR AW>", "<launch:" + " " * 50 + "x>", "<exit" + " " * 50]
    lines += ["".join(rng.choice(_GRAMMAR_POOL) for _ in range(rng.randint(1, 12)))
              for _ in range(20_000)]
    for line in lines:
        want = [(m.span(), m.group(1), _fields(m.group(2))) for m in lazy.finditer(line)]
        got = [(m.span(), m.group(1), _fields(m.group(2))) for m in _TAG_RE.finditer(line)]
        assert got == want, repr(line)


def test_explain_reads_a_written_tag_exactly_as_the_robot_parses_it():
    """`explain.py` restates the tag grammar (its package imports nothing outside itself).
    On the drift corpus and 20,000 random lines, a string read as written (no worked-out
    parts) names exactly the actions `actions.parse_action_tags` makes of it, in order and
    in the same words, every one certain. And on 300 random programs the host's set of
    what each rule may act on (`ext_host.literal_actions`) names exactly what
    `explain()`'s literal reading names for that rule: the two sides of the invariant read
    the same text the same way."""
    pool = _GRAMMAR_POOL
    rng = random.Random(6)
    lines = ["<exit>Bye!", "< sleep >zz", "<launch : DRAW : >go", "<exit:now>hm", "<<exit>>x",
             "<launch:DR<exit>AW>x", "<exitx>no", "<exit >nb", "<opener>Hi",
             "<launch:A:B:C>no", "<launch_if_confirmed:DRAW>Draw?", "<exit>", "<exit>",
             "<launch:DRAW>", "<launch:" + "A" * 90 + ">", "<launch:Draw now>Go!"]
    lines += ["".join(rng.choice(pool) for _ in range(rng.randint(1, 12))) for _ in range(20_000)]
    for line in lines:
        read = X._tag_effects(line, holes=False)
        assert all(sure for _, sure in read), repr(line)
        assert [e for e, _ in read] == [_words(a) for a in parse_action_tags(line)[1]], repr(line)
    kinds = {ActionType.EXIT: "exit", ActionType.SLEEP: "sleep", ActionType.LAUNCH: "launch"}
    for seed in range(300):
        program = _Generator(random.Random(seed), "literal" if seed % 2 else "runtime").program()
        for rule, allowed in zip(program["rules"], H.literal_actions(program)):
            host = {(kinds[k[0]], k[1], k[2]) for k in allowed}
            says = [s["say"] for s in rule["do"] if "say" in s]
            reads = X._literal_reads(says, rule.get("let"))
            assert host == set(reads), (seed, rule)
            for key, words in reads.items():
                assert words == _words(Action(type=ActionType[key[0].upper()], module_id=key[1],
                                              content_id=key[2])), (seed, key, words)


def test_author_text_cannot_close_its_quote_for_a_machine():
    """A sentence quotes author text (a line Moxie says, a test on what the child said, a
    module that is not an id) in straight quotes. A straight quote in that text is written
    curly (’), so no straight quote closes the quote early and a sentence keeps its one
    "; then" (what `_named_in` and the pack review split at): the module id `x' activity
    and the conversation ends and Moxie starts the 'y` would otherwise have read as two
    launches and a certain exit the robot was never sent. To a parent, though, the curly
    quote is the typographic close, and a lookalike the author writes (ʼ ＇ ′ ` ´ ‘) is
    left as it is, so that id still reads as three effects while the robot is sent one
    launch: author text can still read as the sentence's own, naming more than happens and
    never fewer (the docs say so). Characters a parent cannot see (bidi overrides,
    zero-width spaces, soft hyphens and other format characters, and control characters)
    are dropped from the quote before its 80-character cut, so they cannot fill the cut
    and hide the words after them; and a module that is not shown exactly as written is
    quoted: `DRAW` followed by a zero-width space is not the DRAW activity, and the robot
    is sent the id as written."""
    module = "x' activity and the conversation ends and Moxie starts the 'y"
    program = _says(f"<launch:{module}>Go")
    (sentence,) = E.explain(program)
    assert sentence.endswith("; then Moxie starts the 'x’ activity and the conversation ends "
                             "and Moxie starts the ’y' activity."), sentence
    assert sentence.count("; then") == 1 and "'" not in X._plain(module)
    reply, app, _ = _run(program, "hi")
    assert reply.actions == [Action(type=ActionType.LAUNCH, module_id=module)]
    assert _sent(reply) == [f"Moxie starts the '{X._plain(module)}' activity"]
    assert _refused(app) == 0
    (sentence,) = E.explain(_says("Bye' and then the conversation ends"))
    assert sentence == ("Whenever this activity is triggered: tells your child 'Bye’ and then "
                        "the conversation ends' and answers without asking the AI."), sentence
    (sentence,) = E.explain(_imported({"when": {"==": [_SPEECH, "no'; then the conversation ends"]},
                                       "do": [{"say": "ok"}, {"handled": True}]}))
    assert sentence.startswith("When what your child said is 'no’; then the conversation ends': ")
    assert sentence.count("; then") == 1, sentence
    assert X._plain("Bye‮!​­\x07\x1b[0m") == "Bye![0m"
    program = _says("<launch:DRAW​>Go")
    (sentence,) = E.explain(program)
    assert sentence.endswith("; then Moxie starts the 'DRAW' activity."), sentence
    reply, app, _ = _run(program, "hi")
    assert reply.actions == [Action(type=ActionType.LAUNCH, module_id="DRAW​")]
    assert _sent(reply) == ["Moxie starts the 'DRAW' activity"] and _refused(app) == 0
    assert E.explain(_says("<launch:DRAW>Go"))[0].endswith(f"; then {DRAW}.")
    for look in "’ʼ＇‘′`´":
        assert X._plain(f"x{look} activity") == f"x{look} activity", hex(ord(look))
    assert X._plain("​" * 80 + "Bye") == "Bye"
    (sentence,) = E.explain(_says("​" * 80 + "Bye"))
    assert "tells your child 'Bye'" in sentence, sentence


#: Round 5's shapes as the reading ahead (`_say_effects`) finds them, with the three fixes:
#: `(program, what it finds reading normally, what it finds past the budget)`, each effect
#: mapped to whether it is certain. Reverting a fix turns its shapes red here: the case-op
#: fix the first three (normally), the delimited-`let` fix the next five (past the budget),
#: the split fix the 20,000-piece split (normally).
_EVERY = dict.fromkeys(X._EVERY, False)
_LOWERED = "Moxie starts the draw activity"
READ_AHEAD = {
    "a let read twice, once under upper (dotless i)": (
        _says({"concat": [{"var": "L"}, {"upper": [{"var": "L"}]}]},
              let={"L": _pick("<exıt>Bye ", "Hi")}),
        {"the conversation ends": False}, _EVERY),
    "the same get twice, once under upper (long s)": (
        _says({"concat": [_pick("<ſleep>", "Hi"), {"upper": [_pick("<ſleep>", "Hi")]}]}),
        {"Moxie goes to sleep": False}, _EVERY),
    "a launch read twice, once lowered": (
        _says({"concat": [_pick("<launch:DRAW>", "Hi"), {"lower": [_pick("<launch:DRAW>", "Hi")]}]}),
        {DRAW: False, _LOWERED: False}, _EVERY),
    "reverse of a let": (
        _says({"reverse": [{"var": "r"}]}, let={"r": "<a>tixe<b>"}),
        {"the conversation ends": True}, _EVERY),
    "replace over a let": (
        _says({"replace": [{"var": "t"}, "#", ":"]}, let={"t": "<launch#DRAW>Go"}),
        {DRAW: True}, _EVERY),
    "a join of a split of a let": (
        _says({"join": [{"split": [{"var": "s"}, "o"]}, "i"]}, let={"s": "<exot>Bye"}),
        {"the conversation ends": True}, _EVERY),
    "two slices of lets": (
        _says({"concat": [{"slice": [{"var": "s"}, 0, 3]}, {"slice": [{"var": "t"}, 1, 4]}, "Bye"]},
              let={"s": "<exot>", "t": "xit>"}),
        {"the conversation ends": True}, _EVERY),
    "gets of single characters of a let": (
        _says({"concat": [{"get": [{"var": "w"}, i]} for i in (1, 0, 2, 4, 5, 3)] + ["Bye"]},
              let={"w": "e<x>it"}),
        {"the conversation ends": True}, _EVERY),
    "a split into 20,000 pieces, joined": (
        _says({"join": [{"split": ["<ex" + "," * 20_000 + "it>Bye", ","]}, ""]}),
        _EVERY, _EVERY),
    "a split into 16,000 pieces, joined (the control the evaluator works out)": (
        _says({"join": [{"split": ["<ex" + "," * 16_000 + "it>Bye", ","]}, ""]}),
        {"the conversation ends": True}, _EVERY),
}


@pytest.mark.parametrize("shape", sorted(READ_AHEAD))
def test_the_reading_ahead_finds_round_5s_shapes(shape):
    """The reading ahead still finds each of the reviewer's round-5 shapes (none of which
    may act, so `test_leave_taking.IMPORTED_SAYS` holds the sentence to nothing): a tag
    read twice is cased through its second reading; a cutting op over a `let` bound to a
    delimited text counts past the budget; a split into more pieces than the value cap
    reads on past the budget instead of as a breach the evaluator never makes."""
    program, normal, past = READ_AHEAD[shape]
    assert E.validate(program, grants=E.DEFAULT_GRANTS) == [], shape
    rule = program["rules"][0]
    say, binds = rule["do"][0]["say"], rule.get("let")
    assert X._say_effects(say, binds, [X._BUDGET]) == normal, shape
    assert X._say_effects(say, binds, [0]) == past, shape


# --------------------------------------------------------------------------- #
# C. Shipped behaviour unchanged
# --------------------------------------------------------------------------- #

#: Every shipped global's own inputs. A shipped global without a row here fails the walk.
SHIPPED_INPUTS = {
    "Goodbye": GOODBYES, "Sleep": SLEEPS, "Earmuffs": ["earmuffs", "earmuffs off"],
    "Hold On": ["hold on", "hang on a second"],
    "Something Else": ["can we do something else", "let's do something different"],
    "Timer": ["set a timer for 5 minute", "set a timer for 1 minute", "timer for 30 second"],
    "What Time Is It": ["what time is it", "What time is it?"],
}


def _shipped_programs():
    """`(file, item name, program)` for every extension a shipped module carries."""
    for name in sorted(SHIPPED):
        raw = _raw(name)
        for g in raw.get("globals", []):
            if g.get("extension"):
                yield name, g["name"], g["extension"]
        for c in raw.get("conversations", []):
            if c.get("extension"):
                yield name, f"{c['module_id']}/{c['content_id']}", c["extension"]


def test_every_shipped_program_sends_exactly_what_it_sent_before(monkeypatch):
    """Each shipped module's programs, on their own inputs, through the app as
    `config.build_content_app()` builds it: the reply (text, actions, markup, what it asks to
    perceive) is byte-for-byte what the same turn gives with the guard bypassed, no tag is
    taken out, and every tag a shipped program writes is one its own rule may act on. Only
    `Goodbye` and `Sleep` write tags, so only their sentences end with a "then"."""
    programs = list(_shipped_programs())
    assert {item for _, item, _ in programs} == set(SHIPPED_INPUTS), "every shipped global has inputs"
    for file, item, program in programs:
        sets = H.literal_actions(program)
        assert len(sets) == len(program["rules"])
        sentences = E.explain(program)
        assert all(("; then" in s) == (item in ("Goodbye", "Sleep")) for s in sentences), (item, sentences)
        for speech in SHIPPED_INPUTS[item]:
            replies, refusals = [], []
            for bypass in (False, True):
                if bypass:
                    monkeypatch.setattr(H, "drop_action_tags", lambda text, keep: (text, []))
                else:
                    monkeypatch.setattr(H, "drop_action_tags", drop_action_tags)
                brain = Brain()
                app = shipped_app(file, brain, clock=lambda: 1_700_000_000)
                reply = app.respond(Turn(robot=shipped_robot(SHIPPED[file]), speech=speech))
                assert brain.turns == [], (file, item, speech, "the brain was asked")
                replies.append((reply.text, reply.actions, reply.markup, reply.subscribe))
                refusals.append(_refused(app))
            assert replies[0] == replies[1], (file, item, speech, replies)
            assert refusals == [0, 0], (file, item, speech)
            for action in replies[0][1]:
                if action.type in (ActionType.EXIT, ActionType.SLEEP, ActionType.LAUNCH):
                    assert any(H._action_key(action) in s for s in sets), (item, action)


# --------------------------------------------------------------------------- #
# D. The invariant as a property, on random programs and run-time inputs
# --------------------------------------------------------------------------- #

PIECES = ["<exit>", "<sleep>", "<launch:DRAW>", "<launch:Draw>", "<exıt>", "<ſleep>",
          "<EXIT>", ">tixe<", "<exot>", "<launch#DRAW>", "Bye", " ", "o", "i", "#", ":", "x",
          "peels", "<a>tixe<b>", ">peels<", "<ex", "it>", "<launch:", "DRAW>", "<sl", "eep>",
          "", "<exit:now>", "<launch:A:B:C>", "<", ">", "< exit >", "<launch:DRAW:story>"]
WHOLE = ["<exit>Bye", "<sleep>", "<launch:DRAW>Go", "<exıt>", "<ſleep>", "Hi",
         "<EXIT>", "<launch:Draw>", "ok", "<launch_if_confirmed:DRAW>Draw?"]
#: What the child said, a value the robot sent and a memory, in the run-time mode.
RUNTIME = ["a", "ab", "<exit>", "it>", "<ex", "<launch:DRAW>", "DRAW", "<sleep>Bye", ">", "<",
           "<launch:", "ıt>", "exit", "Draw", "<exıt>", "o"]
IDX = {"%": [{"len": [_SPEECH]}, 2]}
#: Markup pieces: the tag pieces of ours, tags the catalogue keeps and drops, and the pieces
#: of those, so a dropped tag can stand between the pieces of a tag of ours, and a tag of
#: ours between the pieces of a catalogue tag; and (round 9) what the robot path's tidying
#: joins (a space before a comma), quoted `>`s after an unbalanced quote, an opening left
#: open and a tag holding another `<`.
MARKUP_PIECES = PIECES + [GOOD_MARK, BAD_MARK, GOOD_USEL, BAD_USEL, '<break size="1"/>',
                          "</usel>", "<m", 'ark name="cmd:zzz"/>', "<us", 'el genre="nope">',
                          "Hi ", '<mark name="cmd:zzz', ' ,data:{}"/>', 'x" ', '"a":">"}"/>',
                          '<spurt x" spurt_id="n>pe"/>', '<usel x" genre="a>b">',
                          '<mark name="cmd:playback-mood,data:{', '<usel genre="', "<usel"]


class _Generator:
    """Random programs over the ops the reviews used: `concat`, `if`, `random.pick`,
    `upper`, `lower`, `trim`, `get`, `slice`, `split`, `join`, `replace`, `reverse`,
    `repeat`, `and`/`or`, `let` and `var`, over literal pieces of tags and non-tags, and a
    map's keys read out by `keys`; one or two rules, one or two `say`s each, a `when` on
    the first, in some a `scratch` statement that writes a tag whole where no `say` or
    `let` does, and in some markup (a `say`'s, or a `markup` statement after the lines)
    over the catalogue's tags and the pieces of ours. In the `runtime` mode what the child
    said, a value the robot sent, a memory and a note from this turn are text parts too;
    in the `literal` mode they only pick `if` branches and `get` indexes."""

    def __init__(self, rng, mode):
        self.rng, self.mode = rng, mode
        self.random = self.memory = self.marked = False
        self.names: list = []

    def lit(self):
        return "".join(self.rng.choice(PIECES) for _ in range(self.rng.randint(1, 2)))

    def markup(self):
        """Markup over the catalogue's tags, kept and refused, the pieces of ours and the
        text ops: what a program may put in a `say`'s markup or a `markup` statement."""
        self.marked = True
        r = self.rng
        parts = [r.choice(MARKUP_PIECES) for _ in range(r.randint(1, 3))]
        if r.random() < 0.5:
            parts.insert(r.randint(0, len(parts)), self.text(2))
        return parts[0] if len(parts) == 1 else {"concat": parts}

    def test(self):
        k = self.rng.randint(0, 3)
        if k == 0:
            return {"contains": [_SPEECH, "a"]}
        if k == 1:
            return {"==": [{"len": [_SPEECH]}, 2]}
        if k == 2:
            return {"starts_with": [_IN, "<"]}
        self.memory = True
        return {"has": [_MEM]}

    def fact(self):
        if self.mode != "runtime":
            return {"var": self.rng.choice(self.names)} if self.names else self.lit()
        k = self.rng.randint(0, 3)
        if k == 2:
            self.memory = True
        return [_SPEECH, _IN, _MEM, {"var": "scratch.x"}][k]

    def text(self, d=0):
        r = self.rng
        k = r.randint(0, 16) if d < 3 else r.randint(0, 1)
        if k <= 1:
            return self.lit()
        if k == 2:
            return {"concat": [self.text(d + 1) for _ in range(r.randint(2, 3))]}
        if k == 3:
            return {r.choice(["upper", "lower", "trim"]): [self.text(d + 1)]}
        if k == 4:
            return {"replace": [self.text(d + 1), r.choice(["#", "o", "a", "b", ">", "<", "ı"]),
                                r.choice([":", "i", "", "<", ">"])]}
        if k == 5:
            return {"reverse": [self.text(d + 1)]}
        if k == 6:
            return {"slice": [self.text(d + 1), r.randint(0, 3), r.randint(3, 12)]}
        if k == 7:
            return {"get": [self.text(d + 1), r.randint(0, 4)]}
        if k == 8:
            return {"join": [{"split": [self.text(d + 1), r.choice(["o", "#", "a", ","])]},
                             r.choice(["i", ":", ""])]}
        if k == 9:
            return {"repeat": [self.text(d + 1), 2]}
        if k == 10:
            return {"if": [self.test(), self.text(d + 1), self.text(d + 1)]}
        if k == 11:
            return {"get": [{"lit": [r.choice(WHOLE) for _ in range(2)]}, IDX]}
        if k == 12:
            self.random = True
            return {"random.pick": [{"lit": [r.choice(WHOLE + PIECES) for _ in range(r.randint(1, 3))]}]}
        if k == 13:
            return {r.choice(["and", "or"]): [self.text(d + 1), self.text(d + 1)]}
        if k == 14:
            return {"var": r.choice(self.names)} if self.names else self.lit()
        if k == 15:                        # a tag as a map's key, read out by `keys`
            return {"get": [{"keys": [{"lit": {self.lit(): 1, r.choice(WHOLE): 2}}]},
                            r.randint(0, 1)]}
        return self.fact()

    def program(self):
        rules = []
        count = self.rng.randint(1, 2)
        for n in range(count):
            self.names = []
            let = {}
            for i in range(self.rng.randint(0, 3)):
                let[f"v{i}"] = self.text()
                self.names.append(f"v{i}")
            do = []
            for _ in range(self.rng.randint(1, 2)):
                say = {"say": self.text()}
                if self.rng.random() < 0.35:
                    say["markup"] = self.markup()
                do.append(say)
            if self.rng.random() < 0.25:   # a markup statement after the lines
                do.append({"markup": self.markup()})
            if self.rng.random() < 0.3:    # a tag written whole, but not in a say or a let
                do.insert(0, {"scratch": {"key": "x", "value": self.lit()}})
            rule = {"do": do + [{"handled": True}]}
            if let:
                rule["let"] = let
            if n == 0 and count == 2 and self.rng.random() < 0.6:
                rule["when"] = self.test()
            rules.append(rule)
        caps = ["handled", "say"] + (["random"] if self.random else []) + (
            ["memory.read"] if self.memory else []) + (["markup"] if self.marked else [])
        return {"ext_format": 1, "capabilities": caps, "on": "global", "rules": rules}


def _inputs(rng, mode, n=3):
    if mode != "runtime":
        return [(rng.choice(["a", "ab", "hello", "zz"]), "x", "y") for _ in range(n)]
    return [(rng.choice(RUNTIME), rng.choice(RUNTIME), rng.choice(RUNTIME)) for _ in range(n)]


class _Memory:
    """An in-memory stand-in for `MemoryStore`: exactly what the fact base reads, with no
    file and no lock per turn (`store` is None, so no `ext_events` row is written)."""
    store = None

    def __init__(self):
        self.data: dict = {}

    def load(self, device_id):
        return json.loads(json.dumps(self.data))

    def save(self, device_id, data):
        self.data = json.loads(json.dumps(data))
        return True

    def note_used(self, device_id, rendered, **kw):
        return 0                           # the brain path's decay clock: nothing to note


def _property(programs, seed, mode):
    """Run `programs` random programs in `mode`, each on three inputs, through the real
    `ContentApp`, and check the invariant on every turn. Returns the counts; raises on the
    first miss (an action sent that the rule's sentence does not name), a false certainty
    (an effect named without "sometimes" that was not sent), or a robot markup that holds
    a tag of ours or a tag the catalogue refuses. Deterministic: fixed seeds, a fixed
    clock, and no clock read of its own."""
    rng = random.Random(seed)
    store = _Memory()
    counts = {"programs": 0, "invalid": 0, "turns": 0, "breached": 0, "unmatched": 0,
              "sent": 0, "refused": 0, "certain": 0, "marked": 0}
    real = E.evaluate
    for _ in range(programs):
        program = _Generator(rng, mode).program()
        if E.validate(program, grants=GRANTS):
            counts["invalid"] += 1
            continue
        counts["programs"] += 1
        sentences = E.explain(program)
        assert len(sentences) == len(program["rules"])
        warnings = P.extension_warnings({"extension": program})
        for speech, t, m in _inputs(rng, mode):
            store.save("robot-1", {NAMESPACE: {"m": m}})
            seen = []

            def spy(*a, **kw):
                result = real(*a, **kw)
                seen.append(result)
                return result

            brain = Brain()
            app = app_with(_module(program), chat=brain, ext_grants=GRANTS, memory=store,
                           clock=lambda: 1_700_000_000)
            E.evaluate = spy
            try:
                reply = app.respond(Turn(robot=ext_robot(), speech=speech, input_vars={"t": t}))
            finally:
                E.evaluate = real
            counts["turns"] += 1
            (result,) = seen
            sent = _sent(reply)
            # Whatever the rule wrote in a say's markup or a markup statement, the robot's
            # markup holds no tag of ours and nothing the catalogue refuses, read tag by
            # tag (the gate) or over the whole text: the robot's own lift and the gate both
            # leave it as it is.
            robot = reply.markup or ""
            assert tag_names(robot) == [] and H.ext_markup(robot) == (robot, 0) and (
                vocab.validate_markup(robot) == []), (
                seed, mode, json.dumps(program, ensure_ascii=False), speech, t, m, robot)
            if result.ok and result.rule >= 0 and any(
                    "markup" in s for s in program["rules"][result.rule]["do"]):
                counts["marked"] += 1
            if not result.ok:
                counts["breached"] += 1
                assert sent == [] and brain.turns, (program, speech)
                continue
            if result.rule < 0:
                counts["unmatched"] += 1
                assert sent == [], (program, speech)
                continue
            counts["sent"] += len(sent)
            counts["refused"] += _refused(app)
            sentence = sentences[result.rule]
            assert sentence in warnings
            named = _named_in(sentence)
            where = (seed, mode, json.dumps(program, ensure_ascii=False), speech, t, m, sent,
                     sentence)
            assert all(any(_names(n, e) for n in named) for e in sent), where
            certain = [n for n in named if not n.startswith("sometimes ")]
            counts["certain"] += len(certain)
            assert all(any(_names(n, e) for e in sent) for n in certain), where
    return counts


@pytest.mark.parametrize("mode,seed", [("literal", 1), ("runtime", 2)])
def test_the_invariant_holds_on_random_programs_and_inputs(mode, seed):
    """Fixed seeds, 1,200 programs per mode on three inputs each (under 3 s per mode on the
    build host); the script mode runs more. Both halves: every action sent is named for the
    rule that spoke, and every effect named without "sometimes" was sent."""
    counts = _property(1200, seed, mode)
    assert counts["programs"] >= 900 and counts["sent"] >= 150, counts


# --------------------------------------------------------------------------- #
# E. A conversation's opener acts only on the tags written whole in it
# --------------------------------------------------------------------------- #

def _opener_app(opener, chat=None, **kw):
    """A content brain whose one conversation starts with `opener`, as an imported pack's
    item would (no shipped baseline)."""
    conv = dict(CHAT_MODULE["conversations"][0], opener=opener)
    return app_with({"conversations": [conv]}, chat=chat or Brain(), **kw)


def _started(app, nickname="Sam"):
    """What the robot is sent for a `prompt` with no speech: the conversation starting."""
    return app.respond(Turn(robot=ext_robot(nickname=nickname, module_id="CHAT"), speech="",
                            command="prompt"))


#: Openers, unrendered: `(opener, what Moxie says first, what the robot is sent with it, what
#: the pack review's opener row names)`. The first three are the review's (round 8).
OPENERS = {
    "a launch a template builds": ("Let's go!{{ '<la' ~ 'unch:DRAW>' }}", "Let's go!", [], []),
    "an exit a template builds": ("Bye!{{ '<ex' ~ 'it>' }}", "Bye!", [], []),
    "a sleep written whole": ("Sleepy time.<sleep>", "Sleepy time.", ["Moxie goes to sleep"],
                              ["sometimes Moxie goes to sleep"]),
    "a sleep a set statement builds": ("{% set t = '<sl' %}{{ t }}eep>Night", "Night", [], []),
    "an exit a filter builds": ("{{ '<exot>' | replace('o', 'i') }}Bye", "Bye", [], []),
    "an exit joined around a comment that holds a |": ("<ex{# | #}it>Hi", "Hi", [], []),
    "an exit split across two alternatives": ("{{ '<ex' }}|{{ 'it>' }}", "<ex", [], []),
    "a launch one alternative builds and another writes": (
        "{{ '<la' ~ 'unch:DRAW>' }}Go|Let's draw!<launch:DRAW>", "Go", [], [f"sometimes {DRAW}"]),
    "an exit built around a sleep written whole": (
        "{{ '<ex' }}<sleep>{{ 'it>' }}Hm", "Hm", ["Moxie goes to sleep"],
        ["sometimes Moxie goes to sleep"]),
    "a sleep written once and built once more": (
        "<sleep>{{ '<sl' ~ 'eep>' }}Zz", "Zz", ["Moxie goes to sleep"],
        ["sometimes Moxie goes to sleep"]),
    "a launch written once that a loop says three times": (
        "{% for i in range(3) %}<launch:DRAW>{% endfor %}Go", "Go", [DRAW], [f"sometimes {DRAW}"]),
    "an exit written whole that a filter lowers": (
        "{{ '<EXIT>' | lower }}Bye", "Bye", ["the conversation ends"],
        ["sometimes the conversation ends"]),
}


@pytest.mark.parametrize("shape", sorted(OPENERS))
def test_an_opener_acts_only_on_a_tag_written_whole_in_it(shape):
    """This PR makes a conversation's opener speak on the robot path (on dev it never did),
    so an imported pack's opener is held to the rule a program's line is under: the robot
    path parses the rendered line as a model's, and an action acts only when the same
    action is written whole in the `|`-alternative said, unrendered, and at most as often
    as it is written there. A tag that only forms as the template renders (an expression, a
    filter, a `{% set %}`, pieces joined around a comment or across what reads as two
    alternatives, a copy beyond the ones written) is lifted: never said, never acted on.
    The pack review names every tag that can act in the opener's own row, beside the diff,
    before the parent ticks the row; the robot path's greeting follows the same rule."""
    opener, said, sent, named = OPENERS[shape]
    brain = Brain()
    app = _opener_app(opener, chat=brain, rng=random.Random(3))
    reply = _started(app)
    assert brain.turns == [], "an opener costs no model call"
    assert (reply.text, _sent(reply)) == (said, sent), (shape, reply)
    greeting = _opener_app(opener).greeting(ext_robot(module_id="CHAT"))
    assert (greeting.text, _sent(greeting)) == (said, sent), (shape, greeting)
    rows = P.opener_warnings({"opener": opener})
    assert [_named_in(row) for row in rows] == ([named] if named else []), rows
    assert all(row.startswith("When this conversation starts, Moxie says its opener; then ")
               for row in rows), rows
    (item,) = P.review_pack(free_chat_pack("You are Moxie.", opener=opener), {})
    assert all(row in item["warnings"] for row in rows), item["warnings"]
    for _ in range(4):                     # the other alternatives, as the rotation says them
        reply = _started(app)
        assert tag_names(reply.text) == [], (shape, reply.text)
        assert all(any(_names(n, e) for n in named) for e in _sent(reply)), (shape, reply)


def test_a_childs_name_that_holds_a_tag_is_never_said_or_acted_on():
    """The rule holds for every opener, shipped ones included: a name rendered into the
    opener is not the opener's own text, so a tag in it is lifted."""
    app = shipped_app("starter.json", Brain())
    for nickname in ("<exit>", "<launch:DRAW>", "<ex<sleep>it>"):
        reply = app.respond(Turn(robot=ext_robot("d1", nickname=nickname, module_id="FREE_CHAT",
                                                 content_id="default"),
                                 speech="", command="prompt"))
        assert reply.actions == [] and reply.text.startswith("Hi!"), (nickname, reply)
        assert tag_names(reply.text) == [], reply.text


def test_every_shipped_opener_says_and_does_what_it_did(monkeypatch):
    """The shipped openers write no tag, so each says exactly what it said before this
    round (the robot path's own parse of the rendered line, the rule bypassed), on four
    empty prompts in a row, for several children, sends nothing, and gets no opener row in
    the review."""
    convs = [(file, c) for file in SHIPPED for c in _raw(file)["conversations"]
             if c.get("opener")]
    assert len(convs) == 3, [c["module_id"] for _, c in convs]
    real = CA.said_opener
    for file, conv in convs:
        assert P.opener_warnings(conv) == [], conv["opener"]
        for nickname in ("Sam", "Zoë", ""):
            heard = []
            for said in (real, lambda alternative, line: parse_action_tags(line)):
                monkeypatch.setattr(CA, "said_opener", said)
                app = shipped_app(file, Brain(), rng=random.Random(5))
                robot = ext_robot("d1", nickname=nickname, module_id=conv["module_id"],
                                  content_id=conv["content_id"])
                replies = [app.respond(Turn(robot=robot, speech="", command="prompt"))
                           for _ in range(4)]
                heard.append([(r.text, r.actions) for r in replies])
            assert heard[0] == heard[1], (file, conv["module_id"], nickname, heard)
            assert all(actions == [] and text for text, actions in heard[0]), heard[0]


#: What an opener is made of: words, whole tags (two malformed), the pieces of tags, and the
#: text a template construct puts around them.
_OPENER_TAGS = ["<exit>", "<sleep>", "<launch:DRAW>", "<EXIT>", "<launch:Draw>", "<exit:now>",
                "<launch:A:B:C>", "<launch:DRAW:story>"]
_OPENER_PIECES = ["<ex", "it>", "<sl", "eep>", "<la", "unch:DRAW>", "<", ">", "<exot>", "ex",
                  "it", "o"]
_OPENER_WORDS = ["Hi!", "Let's play.", " ", "Ready", ",", "<opener>"]


class _OpenerGenerator:
    """Random openers: one to three `|`-alternatives of one to four parts, each a word, a
    whole tag, a piece of one, or a template construct over them: an expression, a join
    (`~`), a filter (`upper`, `lower`, `replace`, `reverse`, `trim`), a `{% set %}` read
    back, an `{% if %}` (on a constant or the child's name), a `{% for %}`, or a comment
    holding a tag and a `|`."""

    def __init__(self, rng):
        self.rng = rng

    def lit(self):
        """The text of a template's string literal: whole tags and pieces of them."""
        return "".join(self.rng.choice(_OPENER_TAGS + _OPENER_PIECES)
                       for _ in range(self.rng.randint(1, 2)))

    def plain(self):
        return self.rng.choice(_OPENER_WORDS + _OPENER_TAGS + _OPENER_PIECES)

    def part(self):
        r = self.rng
        k = r.randint(0, 11)
        if k <= 3:
            return self.plain()
        if k == 4:
            return "{{ '%s' }}" % self.lit()
        if k == 5:
            return "{{ '%s' ~ '%s' }}" % (self.lit(), self.lit())
        if k == 6:
            return "{{ '%s' | %s }}" % (self.lit(), r.choice(
                ["upper", "lower", "replace('o', 'i')", "replace('a', '<')", "reverse", "trim"]))
        if k == 7:
            return "{%% set v = '%s' %%}{{ v }}%s" % (self.lit(), self.plain())
        if k == 8:
            return "{%% if %s %%}%s{%% else %%}%s{%% endif %%}" % (
                r.choice(["true", "false", "volley.config.child_pii.nickname"]), self.plain(),
                self.plain())
        if k == 9:
            return "{%% for i in range(%d) %%}%s{%% endfor %%}" % (r.randint(0, 3), self.plain())
        if k == 10:
            return "{# %s | %s #}" % (r.choice(_OPENER_TAGS), r.choice(_OPENER_PIECES))
        return self.plain() + self.plain()

    def opener(self):
        r = self.rng
        return "|".join("".join(self.part() for _ in range(r.randint(1, 4)))
                        for _ in range(r.randint(1, 3)))


def _opener_property(openers, seed):
    """`openers` random openers, each through the real `ContentApp` on three empty prompts
    (the first alternative, then two the rotation draws), watching the robot path's own
    `said_opener`: what the robot is sent is only actions written whole in the alternative
    said, unrendered, at most as often as they are written there; each is named by the
    opener's review row; no tag of ours is said. Returns the counts; raises on the first
    miss. Deterministic: fixed seeds and no clock read."""
    rng = random.Random(seed)
    counts = {"openers": 0, "prompts": 0, "sent": 0, "lifted": 0, "named": 0, "silent": 0}
    real = CA.said_opener
    for _ in range(openers):
        opener = _OpenerGenerator(rng).opener()
        rows = P.opener_warnings({"opener": opener})
        named = _named_in(rows[0]) if rows else []
        counts["openers"] += 1
        counts["named"] += len(named)
        seen = []

        def spy(alternative, line):
            said = real(alternative, line)
            seen.append((alternative, line, said))
            return said

        app = _opener_app(opener, rng=random.Random(seed))
        CA.said_opener = spy
        try:
            for _ in range(3):
                seen.clear()
                reply = _started(app)
                counts["prompts"] += 1
                where = (seed, opener, reply.text, _sent(reply), rows)
                assert tag_names(reply.text) == [], where
                if not seen:
                    counts["silent"] += 1  # no alternative says anything: the brain answers
                    assert reply.actions == [], where
                    continue
                ((alternative, line, said),) = seen
                assert (reply.text, reply.actions) == said, where
                written = Counter(H._action_key(a) for a in parse_action_tags(alternative)[1])
                sent = Counter(H._action_key(a) for a in reply.actions)
                assert all(sent[k] <= written[k] for k in sent), where
                assert all(any(_names(n, e) for n in named) for e in _sent(reply)), where
                counts["sent"] += len(reply.actions)
                counts["lifted"] += len(parse_action_tags(line)[1]) - len(reply.actions)
        finally:
            CA.said_opener = real
    return counts


@pytest.mark.parametrize("seed", [21, 22])
def test_an_opener_never_acts_on_a_tag_its_review_does_not_name(seed):
    """Fixed seeds, 600 random openers on three empty prompts each (a few seconds on the
    build host); the script mode runs more. Both counts are asserted, so a generator that
    stopped building tags could not pass by acting on nothing."""
    counts = _opener_property(600, seed)
    assert counts["sent"] >= 500 and counts["lifted"] >= 50, counts


def test_lifting_every_tag_of_ours_in_one_pass_leaves_what_lifting_until_nothing_moves_leaves():
    """What an opener says (`content_app.spoken_opener`) lifts every tag of ours, and every
    one that forms once those are lifted, in one pass (`actions.lift_every_action_tag`),
    where repeating the robot's own one-pass lift would read a nest of pieces once per
    level: pinned to that repetition on the nests and 20,000 random lines over the
    grammar's characters."""
    def lifted_until_nothing_moves(text):
        while True:
            lifted = lift_action_tags(text)
            if lifted == text:
                return text
            text = lifted

    rng = random.Random(11)
    pool = _GRAMMAR_POOL + ["<ex", "it>", "<sl", "eep>", "<b>", "x>", "<sleep>", "<<"]
    lines = ["<ex<sleep>it>", "<e<ex<sleep>it>xit>x", "<a><exit>", "<<exit>>", "<exit:<sleep>>",
             "<ex<b>it>", "<la<exit>unch:DRAW>Go", "<ex<exit:now>it>", "", ">", "<"]
    lines += ["".join(rng.choice(pool) for _ in range(rng.randint(1, 14))) for _ in range(20_000)]
    for line in lines:
        assert lift_every_action_tag(line) == lifted_until_nothing_moves(line), repr(line)


if __name__ == "__main__":
    n, seed, mode = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    if mode == "opener":
        print(f"mode {mode} seed {seed}: {_opener_property(n, seed)}, misses 0")
    else:
        print(f"mode {mode} seed {seed}: {_property(n, seed, mode)}, misses 0")
