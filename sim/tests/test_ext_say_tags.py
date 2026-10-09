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
it cannot.

Hermetic: the real `ContentApp` (`helpers_ext.app_with`; the default grants, plus `random`
and `memory.read` for the programs that declare them) over fake brains that count their own
calls, a tmp store for memory and the `ext_events` ring. The property test runs larger as a
script: `python3 sim/tests/test_ext_say_tags.py PROGRAMS SEED MODE` from the checkout root,
MODE `literal` (tags only in the program's own text) or `runtime` (what the child said, a
memory and an `input_vars` value hold tag pieces and whole tags too).
"""
import importlib
import json
import os
import random
import re
import sys
import time

if __name__ == "__main__":                 # the script mode, from the checkout root
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "mqtt"))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from helpers_ext import CHAT_MODULE, app_with, robot as ext_robot
from moxie_sdk.actions import _fields, _TAG_RE, drop_action_tags, parse_action_tags, tag_names
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
#: declare. The guard does not depend on grants; these only let the ops run.
GRANTS = E.DEFAULT_GRANTS | {"random", "memory.read"}
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
    """A tag in a line's markup or a `markup` statement never acts (the robot path keeps
    markup's text only) and is never spoken either: the robot speaks its markup when it is
    given one, so the host takes every tag of ours out of it, to a fixpoint, before the
    catalogue check, the one the robot's one-pass parse would have exposed included
    (`<ex<sleep>it>Hi`, which that parse spoke as `<exit>Hi`), and counts nothing (markup
    acts on nothing). A `scratch` value is never spoken, an earlier `say`'s written exit is
    replaced by the later line (which carries a built tag, taken out), and a conversation's
    `turn.before` program and the `perceive` path go through the same host."""
    markup = E.DEFAULT_GRANTS | {"markup"}
    for tagged in ({"concat": ["<ex", _SPEECH, ">"]}, "<ex<sleep>it>Hi",
                   {"concat": ["<ex", _SPEECH, "it>Hi"]}):
        for program in (
                _imported({"do": [{"say": "Hi", "markup": tagged}, {"handled": True}]},
                          caps=("handled", "markup", "say")),
                _imported({"do": [{"markup": tagged}, {"say": "Hi"}, {"handled": True}]},
                          caps=("handled", "markup", "say"))):
            assert E.validate(program, grants=markup) == []
            reply, app, _ = _run(program, "<exit>" if tagged == "<ex<sleep>it>Hi" else "it",
                                 grants=markup)
            assert reply.text == "Hi" and reply.actions == [], (tagged, reply)
            assert tag_names(reply.markup or "") == [] and _refused(app) == 0, (tagged, reply)
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


def test_author_text_cannot_read_as_part_of_the_sentence():
    """A sentence quotes author text (a line Moxie says, a test on what the child said, a
    module that is not an id) in straight quotes. A straight quote in that text is written
    curly (’), so it cannot close the quote early: a module id `x' activity and the
    conversation ends and Moxie starts the 'y` read as two launches and a certain exit
    the robot was never sent. Characters a parent cannot see (bidi overrides, zero-width
    spaces, soft hyphens and other format characters, and control characters) are dropped
    from the quote, and a module that is not shown exactly as written is quoted: `DRAW`
    followed by a zero-width space is not the DRAW activity, and the robot is sent the id
    as written."""
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


class _Generator:
    """Random programs over the ops the reviews used: `concat`, `if`, `random.pick`,
    `upper`, `lower`, `trim`, `get`, `slice`, `split`, `join`, `replace`, `reverse`,
    `repeat`, `and`/`or`, `let` and `var`, over literal pieces of tags and non-tags, and a
    map's keys read out by `keys`; one or two rules, one or two `say`s each, a `when` on
    the first, and in some a `scratch` statement that writes a tag whole where no `say` or
    `let` does. In the `runtime` mode what the child said, a value the robot sent, a
    memory and a note from this turn are text parts too; in the `literal` mode they only
    pick `if` branches and `get` indexes."""

    def __init__(self, rng, mode):
        self.rng, self.mode = rng, mode
        self.random = self.memory = False
        self.names: list = []

    def lit(self):
        return "".join(self.rng.choice(PIECES) for _ in range(self.rng.randint(1, 2)))

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
            do = [{"say": self.text()} for _ in range(self.rng.randint(1, 2))]
            if self.rng.random() < 0.3:    # a tag written whole, but not in a say or a let
                do.insert(0, {"scratch": {"key": "x", "value": self.lit()}})
            rule = {"do": do + [{"handled": True}]}
            if let:
                rule["let"] = let
            if n == 0 and count == 2 and self.rng.random() < 0.6:
                rule["when"] = self.test()
            rules.append(rule)
        caps = ["handled", "say"] + (["random"] if self.random else []) + (
            ["memory.read"] if self.memory else [])
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
    first miss (an action sent that the rule's sentence does not name) or a false
    certainty (an effect named without "sometimes" that was not sent). Deterministic:
    fixed seeds, a fixed clock, and no clock read of its own."""
    rng = random.Random(seed)
    store = _Memory()
    counts = {"programs": 0, "invalid": 0, "turns": 0, "breached": 0, "unmatched": 0,
              "sent": 0, "refused": 0, "certain": 0}
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


if __name__ == "__main__":
    n, seed, mode = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    print(f"mode {mode} seed {seed}: {_property(n, seed, mode)}, misses 0")
