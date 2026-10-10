"""Pack content cannot send robot system commands (K5.2, the follow-up #312 named).

A content pack's text reaches the robot three ways: an extension's `say` line, a
conversation's opener, and (under the `markup` grant) a `say`'s markup or a `markup`
statement. Before this change the first two went to the robot as written: the floor speaks
a line holding `<` as it is, so `<mark name="cmd:start-systemunpair"/>Hi!` in an imported
opener or line put the catalogue's unpair verb in the robot's `text` and `markup`, the pack
review said nothing and pre-ticked the item, and under the markup grant `robot_markup` kept
both system verbs because both are catalogue verbs. Whether a robot acts on them from a chat
line is unverified without hardware; this treats it as real.

Now every mark pack content writes passes one gate (`ext_host.pack_line`, `pack_spoken`,
`pack_markup`): a mark stays only when it is one this appliance could have minted itself:
the catalogue's own pattern reads it whole, its verb is in `vocab.EXPRESSIVE_VERBS` (a
face, a gesture, a sound, the screen icons) and every id in it is in the catalogue; every
other `<mark` opening is cut, never spoken, and a catalogue verb among them is told to the
parent once per robot, item and verb through the `ext_events` ring. The system verbs
(`vocab.SYSTEM_VERBS`, enumerated from the catalogue by name) are never sent. The review
names every command a pack's text writes in its own row and un-ticks an item that carries
a system verb. Shipped content and the trusted Python handlers are unchanged, walked here.

Hermetic: the real `ContentApp` over fake brains, the real `MoxieRuntime` over a fake
transport for what reaches the wire, a tmp store for the `ext_events` ring.
"""
import json
import os
import random
import re
import time

import pytest

from helpers_content import free_chat_pack
from helpers_ext import CHAT_MODULE, app_with, robot as ext_robot
from moxie_sdk import automarkup, vocab
from moxie_sdk.actions import lift_every_action_tag, parse_action_tags, tag_names
from moxie_sdk.content import content_app as CA
from moxie_sdk.content import ext as E
from moxie_sdk.content import ext_host as H
from moxie_sdk.content import packs as P
from moxie_sdk.memory_store import MemoryStore
from moxie_sdk.store import JsonStore
from moxie_sdk.types import ActionType, Turn
from test_leave_taking import (Brain, SHIPPED, _hard_limit, _imported, _raw, _says, _Stalled,
                               shipped_app, robot as shipped_robot)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

UNPAIR = '<mark name="cmd:start-systemunpair"/>'
SUSPEND = '<mark name="cmd:start-systemsuspend"/>'
#: A verb in the catalogue that is not expressive: cut and named, but not a system verb.
SCRIPTED = '<mark name="cmd:scripted"/>'
#: A mark this appliance mints itself: a face. Pack content may send it.
MOOD = '<mark name="cmd:playback-mood,data:{+mood+:1,+intensity+:1}"/>'
#: A gesture, the other mark the floor mints on every line.
GESTURE = vocab.tree_mark("Gesture_None")
SYSTEM = ("start-systemunpair", "start-systemsuspend")
#: Anything the robot could read as the unpair or suspend verb, in any case.
_SYSTEM_RE = re.compile(r"start-system", re.I)


def _module(program, name="Probe"):
    """`program` as the only global, matching every non-empty utterance."""
    return dict(CHAT_MODULE, globals=[{"name": name, "pattern": r"[\s\S]", "extension": program}])


def _opener_module(opener):
    return {"conversations": [dict(CHAT_MODULE["conversations"][0], opener=opener)]}


def _rows(store, device="robot-1"):
    return store.store.read(device, CA.EXT_EVENTS_COLLECTION, [])


def _clean(text) -> bool:
    """No system verb, in any case, and no `<mark` opening left that is not a mark this
    appliance could have minted: read whole by the catalogue's own pattern, an expressive
    verb, every id in the catalogue."""
    if _SYSTEM_RE.search(text or ""):
        return False
    for m in re.finditer(r"<mark\b[^>]*>?", text or "", re.I):
        read = vocab._MARK_RE.fullmatch(m.group(0))
        if (read is None or read.group(1) not in vocab.EXPRESSIVE_VERBS
                or vocab.validate_markup(m.group(0))):
            return False
    return True


def _wire(app, speech, *, command="prompt", module_id="CHAT", tmp_path, nickname="Sam"):
    """One turn through the real runtime: the `output` the robot is sent."""
    pytest.importorskip("paho.mqtt.client", reason="the runtime imports paho")
    from helpers_runtime import drive_turn, make_runtime
    rt, device = make_runtime(app, module_id=module_id, nickname=nickname,
                              store=JsonStore(root=str(tmp_path)))
    rt.brain_budget_s = 0
    return drive_turn(rt, device, speech, command=command)["output"]


# --------------------------------------------------------------------------- #
# A. The catalogue's system verbs and the verbs a pack may send
# --------------------------------------------------------------------------- #

def test_the_system_verbs_are_enumerated_from_the_catalogue_and_never_expressive():
    """The deny set is read off `vocab.VERBS` by name (anything that starts a system flow:
    `start-system*`, wifi, pairing, reset, update, suspend), so a verb added to the
    catalogue later lands in it on its own; today it is exactly the two recovered ones.
    The allow set is the verbs this appliance mints for a line itself and whose payload
    `validate_markup` reads, every one in the catalogue and none a system verb."""
    assert vocab.SYSTEM_VERBS == frozenset(SYSTEM)
    assert vocab.SYSTEM_VERBS <= vocab.VERB_SET
    for verb in SYSTEM + ("start-systemupdate", "wifi-setup", "pair", "factory-reset",
                          "START-SYSTEMUNPAIR", "reboot", "update-firmware"):
        assert vocab.is_system_verb(verb), verb
    for verb in vocab.EXPRESSIVE_VERBS | {"behaviour-tree", "playback-mood", "playaudio",
                                           "stopaudio", "icons-v2", "vocal-gesture"}:
        assert not vocab.is_system_verb(verb), verb
    assert vocab.EXPRESSIVE_VERBS == frozenset({"behaviour-tree", "playback-mood", "vocal-gesture",
                                                "playaudio", "stopaudio", "icons-v2"})
    assert vocab.EXPRESSIVE_VERBS <= vocab.VERB_SET
    assert vocab.EXPRESSIVE_VERBS.isdisjoint(vocab.SYSTEM_VERBS)
    # Every mark the floor and the planner mint is one a pack may send too.
    for minted in (vocab.mood_mark(1), vocab.tree_mark("Gesture_Talk"), vocab.icons_mark(["School"]),
                   vocab.audio_mark(vocab.SFX_STINGER), vocab.mark("stopaudio", {"Scope": 0})):
        assert H.pack_text(minted) == (minted, []), minted


def test_a_marks_verb_is_read_however_it_is_written():
    """`mark_verbs` names what a robot might read as a verb, for the review and the parent's
    row: any quoting, any case, spaces around the `=`, a mark left open, data or none; the
    verb is returned as written (the catalogue is case-sensitive, the report lower-cases)."""
    assert vocab.mark_verbs(UNPAIR + " and " + MOOD) == ["start-systemunpair", "playback-mood"]
    assert vocab.mark_verbs("<MARK NAME='CMD:START-SYSTEMUNPAIR'/>") == ["START-SYSTEMUNPAIR"]
    assert vocab.mark_verbs('<mark name = "cmd:scripted" />') == ["scripted"]
    assert vocab.mark_verbs('Hi <mark name="cmd:start-systemsuspend') == ["start-systemsuspend"]
    assert vocab.mark_verbs('<mark name="x"/> <usel genre="question">Hi</usel>') == []
    assert vocab.mark_verbs("") == [] and vocab.mark_verbs(None) == []


# --------------------------------------------------------------------------- #
# B. A pack's line, opener and markup cannot put a system verb on the robot
# --------------------------------------------------------------------------- #

#: A `say` line under the default grants: `(what the rule says, what the robot is sent,
#: the actions sent, the catalogue verbs told to the parent, whether a tag of ours was
#: taken out too)`. Each shape put the system verb in the robot's `text` and `markup`
#: before this change.
SAYS = {
    "a system mark before the words": (UNPAIR + "Okay!", "Okay!", [], ["start-systemunpair"], False),
    "a system mark after the words": ("Okay!" + SUSPEND, "Okay!", [], ["start-systemsuspend"], False),
    "the mark alone": (UNPAIR, "", [], ["start-systemunpair"], False),
    "both system verbs": (UNPAIR + "Okay!" + SUSPEND, "Okay!", [],
                          ["start-systemunpair", "start-systemsuspend"], False),
    "in upper case": ('<MARK NAME="CMD:START-SYSTEMUNPAIR"/>Okay!', "Okay!", [],
                      ["start-systemunpair"], False),
    "in single quotes": ("<mark name='cmd:start-systemunpair'/>Okay!", "Okay!", [],
                         ["start-systemunpair"], False),
    "with spaces around the =": ('<mark name = "cmd:start-systemunpair" />Okay!', "Okay!", [],
                                 ["start-systemunpair"], False),
    "with data": ('<mark name="cmd:start-systemunpair,data:{+a+:1}"/>Okay!', "Okay!", [],
                  ["start-systemunpair"], False),
    "left open, which takes the rest of the line with it": (
        'Okay! <mark name="cmd:start-systemunpair" and more', "Okay!", [], ["start-systemunpair"],
        False),
    "built from pieces at run time": (
        {"concat": ["<ma", 'rk name="cmd:start-system', "unpair\"/>Okay!"]}, "Okay!", [],
        ["start-systemunpair"], False),
    "read from what the child said": ({"var": "speech"}, "hi", [], ["start-systemunpair"], False),
    "a verb the catalogue has but an activity may not send": (
        SCRIPTED + "Okay!", "Okay!", [], ["scripted"], False),
    "a verb the catalogue does not have is a catalogue drop, not a command": (
        '<mark name="cmd:zzz"/>Okay!', "Okay!", [], [], False),
    "an expressive mark stays, a system one goes": (
        MOOD + UNPAIR + "Okay!", MOOD + "Okay!", [], ["start-systemunpair"], False),
    "an expressive mark in upper case is not one this appliance mints, and goes unnamed": (
        '<MARK NAME="CMD:PLAYBACK-MOOD"/>Okay!', "Okay!", [], [], False),
    "an expressive verb with an id the catalogue does not have goes, unnamed": (
        '<mark name="cmd:playback-mood,data:{+mood+:42,+intensity+:1}"/>Okay!', "Okay!", [], [],
        False),
    "an expressive verb with an invented sound goes, unnamed": (
        '<mark name="cmd:playaudio,data:{+SoundToPlay+:+nope+}"/>Okay!', "Okay!", [], [], False),
    # The pieces of a tag of ours around a mark, and of a mark around a tag of ours.
    "a tag of ours that forms once the mark is cut is taken out, never acted on": (
        "<ex" + UNPAIR + "it>Bye", "Bye", [], ["start-systemunpair"], True),
    "a mark that forms once the robot lifts a kept exit is cut, the exit kept": (
        '<ma<exit>rk name="cmd:start-systemunpair"/>Bye', "Bye", [ActionType.EXIT],
        ["start-systemunpair"], False),
    "a mark that forms once a lifted sleep's pieces meet": (
        "<ma<sl<mark name=\"cmd:zzz\"/>eep>rk name=\"cmd:start-systemsuspend\"/>Night", "Night",
        [], ["start-systemsuspend"], True),
    "a mark holding a kept exit is cut around it, and the exit acts": (
        '<mark na<exit>me="cmd:start-systemunpair"/>Bye', "Bye", [ActionType.EXIT],
        ["start-systemunpair"], False),
}


def _say_program(say, *, exit_whole=False):
    rule = {"do": [{"say": say}, {"handled": True}]}
    if exit_whole:
        rule["let"] = {"t": "<exit>"}              # the exit is written whole: it may act
    return _imported(rule)


@pytest.mark.parametrize("shape", sorted(SAYS))
def test_a_packs_say_line_cannot_send_a_system_command(shape, tmp_path):
    """Through the real app under the default grants: the line is said without the mark,
    the robot's text and the floor's markup hold no system verb, the model is not asked,
    nothing is a breach or a refusal of a tag of ours, and the parent is told once per
    verb through the `ext_events` ring, with a fixed sentence (never author text)."""
    say, said, acts, told, tag_taken = SAYS[shape]
    program = _say_program(say, exit_whole="kept exit" in shape)
    assert E.validate(program, grants=E.DEFAULT_GRANTS) == [], shape
    store = MemoryStore(JsonStore(str(tmp_path)))
    brain = Brain()
    app = app_with(_module(program), chat=brain, memory=store, clock=lambda: 1_700_000_000)
    speech = UNPAIR + "hi" if "child said" in shape else "hi"
    reply = app.respond(Turn(robot=ext_robot(), speech=speech))
    assert brain.turns == [], shape
    assert reply.text == said, (shape, reply.text)
    assert [a.type for a in reply.actions] == acts, (shape, reply.actions)
    assert _clean(reply.text) and _clean(reply.markup or ""), (shape, reply)
    assert tag_names(reply.text) == [], reply.text
    assert not app._ext_breaches, shape
    assert (sum(app._ext_refusals.values()) > 0) == tag_taken, (shape, app._ext_refusals)
    # A tag of ours taken out is told as #312 tells it, in its own row, before the commands.
    expected = ([H.REFUSED_TAG_REASON] if tag_taken else []) + [f"command:{v}" for v in told]
    rows = _rows(store)
    assert [r["reason"] for r in rows] == expected, (shape, rows)
    for row in rows:
        assert (row["extension"], row["hook"], row["quarantined"]) == ("global:Probe", "global", False)
        if row["reason"] == H.REFUSED_TAG_REASON:
            continue
        verb = row["reason"].split(":", 1)[1]
        assert row["sentence"] == H.refused_command_words(verb), row
        assert "unpair" in row["sentence"] if verb == "start-systemunpair" else True, row
        assert "system command" in row["sentence"] if verb in SYSTEM else "may not" in row["sentence"]
    # Three more turns: still one row per verb (told once), the program still runs.
    for _ in range(3):
        again = app.respond(Turn(robot=ext_robot(), speech=speech))
        assert again.text == said and brain.turns == []
    assert [r["reason"] for r in _rows(store)] == expected
    assert sum(app._ext_commands_refused.values()) == 4 * len(told), app._ext_commands_refused
    # Another robot is told on its own.
    app.respond(Turn(robot=ext_robot("robot-2"), speech=speech))
    assert len(_rows(store, "robot-2")) == len(expected)


@pytest.mark.parametrize("shape", ["a system mark before the words", "in upper case",
                                   "a mark that forms once the robot lifts a kept exit is cut, the exit kept",
                                   "an expressive mark stays, a system one goes"])
def test_what_reaches_the_wire_from_a_packs_line_holds_no_system_verb(shape, tmp_path):
    """The same lines through the real runtime: the robot's `output.text` and the
    `output.markup` the floor made of it hold no system verb; an expressive mark the pack
    wrote is still in both, as before this change."""
    say, said, acts, _, _ = SAYS[shape]
    app = app_with(_module(_say_program(say, exit_whole="kept exit" in shape)), chat=Brain())
    out = _wire(app, "hi", tmp_path=tmp_path)
    assert out["text"] == said, (shape, out)
    assert _clean(out["text"]) and _clean(out["markup"]), (shape, out)
    assert out["markup"].strip(), "the line is still performed"
    if MOOD in said:
        assert MOOD in out["text"] and MOOD in out["markup"], out


#: Openers, unrendered: `(opener, what Moxie says first, the verbs told to the parent)`.
OPENERS = {
    "the review's repro": (UNPAIR + "Hi {{ volley.config.child_pii.nickname }}! Let's chat.",
                           "Hi Sam! Let's chat.", ["start-systemunpair"]),
    "a template builds the mark": ("{{ '<ma' ~ 'rk name=\"cmd:start-systemunpair\"/>' }}Hi",
                                   "Hi", ["start-systemunpair"]),
    "a set statement builds it": ("{% set t = '<mark name=\"cmd:start-system' %}{{ t }}suspend\"/>Hi",
                                  "Hi", ["start-systemsuspend"]),
    "in upper case and single quotes": ("<MARK NAME='CMD:START-SYSTEMUNPAIR'/>Hi", "Hi",
                                        ["start-systemunpair"]),
    "the mark alone is said as nothing, as an opener that is only a tag of ours is": (
        UNPAIR, "", ["start-systemunpair"]),
    "an exit forms once the mark is cut, and is lifted": ("<ex" + UNPAIR + "it>Hi", "Hi",
                                                           ["start-systemunpair"]),
    "an expressive mark stays": (MOOD + "Hi", MOOD + "Hi", []),
    "an expressive verb with an id the catalogue does not have goes, unnamed": (
        '<mark name="cmd:behaviour-tree,data:{+eventName+:+Gesture_Zap+}"/>Hi', "Hi", []),
    "a verb an activity may not send goes, named": (SCRIPTED + "Hi", "Hi", ["scripted"]),
    # Two alternatives: the second prompt must say the other one, so the mark is met.
    "in the second of two alternatives": ("Hi|" + SUSPEND + "Hey", "Hi", ["start-systemsuspend"]),
}


@pytest.mark.parametrize("shape", sorted(OPENERS))
def test_an_opener_cannot_send_a_system_command(shape, tmp_path):
    """Through the real app, then the real runtime: the opener is said without the mark,
    the parent is told once per verb in the conversation's own `ext_events` row (hook
    `opener`), and what the robot is sent holds no system verb. An opener that is only a
    system mark is said as nothing and costs no model call, as one that is only a tag of
    ours is (the property in test_ext_say_tags pins that reply)."""
    opener, said, told = OPENERS[shape]
    store = MemoryStore(JsonStore(str(tmp_path / "store")))
    brain = Brain()
    app = app_with(_opener_module(opener), chat=brain, memory=store, rng=random.Random(3),
                   clock=lambda: 1_700_000_000)
    replies = [app.respond(Turn(robot=ext_robot(module_id="CHAT"), speech="", command="prompt"))
               for _ in range(4)]
    assert brain.turns == [], "an opener costs no model call"
    assert replies[0].text == said, (shape, replies[0])
    for reply in replies:
        assert reply.actions == [] and _clean(reply.text) and _clean(reply.markup or ""), reply
        assert tag_names(reply.text) == [], reply.text
    rows = _rows(store)
    assert sorted(r["reason"] for r in rows) == sorted(f"command:{v}" for v in told), rows
    for row in rows:
        assert (row["extension"], row["hook"]) == ("conversation:CHAT/default", "opener"), row
        assert row["sentence"] == H.refused_command_words(row["reason"].split(":", 1)[1])
    if said:
        out = _wire(app_with(_opener_module(opener), chat=Brain(), rng=random.Random(3)), "",
                    tmp_path=tmp_path / "wire")
        assert out["text"] == said and _clean(out["text"]) and _clean(out["markup"]), out
        assert out["markup"].strip()


def test_a_childs_name_that_holds_a_system_mark_is_never_sent():
    """The shipped Free Chat opener renders the child's name into the line. A name that
    is a system mark (the name rule refuses `<`, but the gate does not rely on it) is cut
    like any other mark pack content did not write: the hello is said without it."""
    app = shipped_app("starter.json", Brain())
    for nickname in (UNPAIR, "Sam" + SUSPEND, "<MARK NAME='CMD:START-SYSTEMUNPAIR'/>"):
        reply = app.respond(Turn(robot=ext_robot("d1", nickname=nickname, module_id="FREE_CHAT",
                                                 content_id="default"),
                                 speech="", command="prompt"))
        assert reply.actions == [] and _clean(reply.text), (nickname, reply)
        assert reply.text.startswith("Hi") and reply.text.endswith("What do you want to talk about?")


#: Markup under the `markup` grant: `(markup, what robot_markup leaves, tags dropped, verbs)`.
MARKUPS = {
    "a system verb is a catalogue verb, and still goes": (UNPAIR + "Hi", "Hi", 1, ["start-systemunpair"]),
    "the other one too": (SUSPEND + "Hi", "Hi", 1, ["start-systemsuspend"]),
    "a verb an activity may not send": (SCRIPTED + "Hi", "Hi", 1, ["scripted"]),
    "an expressive mark stays": (MOOD + "Hi", MOOD + "Hi", 0, []),
    "a system mark that forms once a dropped tag's pieces meet drops the markup whole": (
        '<ma<mark name="cmd:zzz"/>rk name="cmd:start-systemunpair"/>Hi', "", 2, ["start-systemunpair"]),
    # The robot's lift runs before the gate, so a mark that forms once an exit is lifted
    # is one the gate sees, and drops tag by tag.
    "a system mark that forms once the robot lifts an exit is dropped": (
        '<ma<exit>rk name="cmd:start-systemsuspend"/>Hi', "Hi", 1, ["start-systemsuspend"]),
    "in upper case the catalogue refuses the verb, a catalogue drop": (
        '<MARK NAME="CMD:START-SYSTEMUNPAIR"/>Hi', "Hi", 1, ["start-systemunpair"]),
}


@pytest.mark.parametrize("shape", sorted(MARKUPS))
def test_a_packs_markup_cannot_send_a_system_command(shape, tmp_path):
    """`robot_markup` keeps its contract (`(clean, dropped)`, pinned in test_ext_say_tags)
    and now drops a mark whose verb an activity may not send, system verbs first among
    them; `pack_markup` names the verbs, which the host tells the parent. Through the real
    app for a `say`'s markup and a `markup` statement, and through the runtime."""
    markup, clean, dropped, told = MARKUPS[shape]
    assert H.robot_markup(markup) == (clean, dropped), shape
    assert H.pack_markup(markup) == (clean, dropped, told), shape
    assert _clean(clean)
    grants = E.DEFAULT_GRANTS | {"markup"}
    programs = (_imported({"do": [{"say": "Hi", "markup": markup}, {"handled": True}]},
                          caps=("handled", "markup", "say")),
                _imported({"do": [{"say": "Hi"}, {"markup": markup}, {"handled": True}]},
                          caps=("handled", "markup", "say")))
    for i, program in enumerate(programs):
        assert E.validate(program, grants=grants) == []
        store = MemoryStore(JsonStore(str(tmp_path / f"s{i}")))
        app = app_with(_module(program), chat=Brain(), ext_grants=grants, memory=store,
                       clock=lambda: 1_700_000_000)
        reply = app.respond(Turn(robot=ext_robot(), speech="hi"))
        assert reply.text == "Hi" and reply.actions == [], reply
        assert _clean(reply.markup or ""), reply.markup
        assert (reply.markup is None) == (clean == ""), reply.markup
        assert [r["reason"] for r in _rows(store)] == [f"command:{v}" for v in told]
        assert not app._ext_breaches and not app._ext_refusals
    app = app_with(_module(program), chat=Brain(), ext_grants=grants)
    out = _wire(app, "hi", tmp_path=tmp_path / "wire")
    assert out["text"] == "Hi" and _clean(out["markup"]) and out["markup"].strip(), out


def test_a_line_of_pieces_nested_past_the_gates_rounds_is_not_spoken():
    """Cutting a mark can make the pieces of a tag of ours meet, and lifting a tag the
    pieces of a mark, so the gate alternates the two; it gives up after
    `PACK_GATE_ROUNDS` rounds and speaks nothing of such a line rather than read a nest
    once per level (a pack may carry a megabyte of them). A nest within the rounds is
    cleared; one past them is silenced, both in bounded time."""
    inner = UNPAIR
    for _ in range(H.PACK_GATE_ROUNDS - 2):
        inner = "<ma<ex" + inner + "it>rk name=\"cmd:start-systemunpair\"/>"
    assert H.pack_spoken(inner + "Hi") == ("Hi", ["start-systemunpair"] * (H.PACK_GATE_ROUNDS - 1))
    deep = UNPAIR
    for _ in range(H.PACK_GATE_ROUNDS + 4):
        deep = "<ma<ex" + deep + "it>rk name=\"cmd:start-systemunpair\"/>"
    started = time.perf_counter()
    try:
        with _hard_limit(5.0):
            text, verbs = H.pack_spoken(deep + "Hi")
            line, taken, verbs2 = H.pack_line(deep + "Hi", lambda a: False)
    except _Stalled:
        pytest.fail("still clearing a nest after 5 s")
    assert text == "" and line == "" and _clean(text) and verbs and verbs2
    assert time.perf_counter() - started < 0.5
    reply, app, _ = _run_say({"concat": [deep, "Hi"]})
    assert reply.text == "" and reply.actions == []


def _run_say(say, speech="hi", **kw):
    brain = Brain()
    app = app_with(_module(_says(say)), chat=brain, **kw)
    return app.respond(Turn(robot=ext_robot(), speech=speech)), app, brain


def test_the_gate_reads_a_mark_opening_in_any_case():
    """`<MARK` is `<mark` to the robot's reader as far as anyone knows, so every pattern
    that finds a mark opening is case-insensitive: `_EXT_MARK` (dropping `re.I` from it was
    the surviving mutant of #312's last round: an upper-case mark the catalogue's pattern
    cannot read was kept as sound), `pack_text` and `mark_verbs`."""
    assert H.ext_markup("<MARK name='cmd:playback-mood'/>Hi") == ("Hi", 1)
    assert H.robot_markup("<MARK name='cmd:playback-mood'/>Hi") == ("Hi", 1)
    assert H._malformed_tag("<MARK name='cmd:playback-mood'/>")
    assert H.pack_text("<MARK name='cmd:playback-mood'/>Hi") == ("Hi", [])
    assert H.pack_text('<Mark name="cmd:START-SYSTEMUNPAIR"/>Hi') == ("Hi", ["start-systemunpair"])


# --------------------------------------------------------------------------- #
# C. The review names every command in a pack's text, and un-ticks a system verb
# --------------------------------------------------------------------------- #

def test_the_review_names_the_commands_a_packs_text_writes_and_unticks_a_system_verb():
    """One row per item naming every catalogue verb a `<mark` in any of its strings
    (opener, prompt, a program's lines) asks for, which of them are never sent, and whether
    one is a system command; an item with a system verb is not pre-ticked whatever its
    state, as an escalation is not. A verb the catalogue does not have is counted, never
    quoted (author text). An item with no mark gets no row and ticks as before."""
    item, = P.review_pack(free_chat_pack("You are Moxie.", opener=UNPAIR + "Hi!"), {})
    assert item["state"] == P.NEW and item["default"] is False, item
    assert item["commands"] == ["start-systemunpair"] and item["system_commands"] == ["start-systemunpair"]
    rows = [w for w in item["warnings"] if "command" in w]
    assert rows == P.command_warnings(item["commands"]), item["warnings"]
    assert len(rows) == 2 and "cmd:start-systemunpair" in rows[0], rows
    assert "system command" in rows[1] and "unpair" in rows[1] and "without" in rows[1], rows
    # The same verb in the prompt alone, or in upper case and single quotes: named, un-ticked.
    for prompt in ("Always say <MARK NAME='CMD:START-SYSTEMSUSPEND'/> first.",
                   "Say " + SUSPEND):
        item, = P.review_pack(free_chat_pack(prompt), {})
        assert item["default"] is False and item["system_commands"] == ["start-systemsuspend"], item
    # An expressive mark: named, sent, still ticked. A verb an activity may not send:
    # named as never sent, still ticked. An unknown verb: counted, never quoted.
    item, = P.review_pack(free_chat_pack("You are Moxie.", opener=MOOD + "Hi!"), {})
    assert item["default"] is True and item["commands"] == ["playback-mood"]
    assert item["system_commands"] == [] and any("cmd:playback-mood" in w for w in item["warnings"])
    assert not any("never sends" in w or "without" in w for w in item["warnings"]), item["warnings"]
    item, = P.review_pack(free_chat_pack("You are Moxie.", opener=SCRIPTED + '<mark name="cmd:zzz"/>Hi!'), {})
    assert item["default"] is True and item["commands"] == ["scripted"]
    assert any("cmd:scripted" in w and "never sends" in w for w in item["warnings"]), item["warnings"]
    assert not any("zzz" in w for w in item["warnings"]), item["warnings"]
    assert any("1 it does not know" in w for w in item["warnings"]), item["warnings"]
    # A program's line in a global.
    program = _says(UNPAIR + "Okay!")
    pack = P.export_pack([{"kind": "global", "key": "Probe", "source_version": 1,
                           "data": {"name": "Probe", "pattern": "hi", "extension": program}}],
                         name="p", pack_id="p", now=1788400000)
    item, = P.review_pack(pack, {})
    assert item["default"] is False and item["system_commands"] == ["start-systemunpair"], item
    # No mark: no row, ticked, and no new key clutter in the warnings.
    item, = P.review_pack(free_chat_pack("You are Moxie."), {})
    assert item["default"] is True and item["commands"] == [] and item["system_commands"] == []
    assert not any("command" in w for w in item["warnings"]), item["warnings"]
    assert P.command_warnings([]) == []


# --------------------------------------------------------------------------- #
# D. Shipped content and the trusted handlers are unchanged
# --------------------------------------------------------------------------- #

def _shipped_texts():
    """Every string a shipped module's items carry, with where it is."""
    for name in sorted(SHIPPED):
        raw = _raw(name)
        for c in raw.get("conversations", []):
            for field in ("opener", "prompt"):
                if c.get(field):
                    yield (name, f"{c['module_id']}/{c['content_id']}", field), c[field]
            for text in H._strings_in(c.get("extension") or {}, []):
                yield (name, f"{c['module_id']}/{c['content_id']}", "extension"), text
        for g in raw.get("globals", []):
            for text in H._strings_in(g.get("extension") or {}, []):
                yield (name, g["name"], "extension"), text


def test_every_shipped_text_passes_the_gate_untouched_and_gets_no_command_row(monkeypatch):
    """A walk over both shipped modules: no string any shipped item carries holds a
    `<mark` at all, so the gate leaves each as it is and names nothing; every shipped
    item's review row has no command row and ticks as it did; and every shipped program,
    on its own inputs, and every shipped opener, for several children, sends byte for
    byte what it sends with the gate bypassed."""
    texts = list(_shipped_texts())
    assert len(texts) >= 30, "the walk found the shipped text"
    for where, text in texts:
        assert vocab.mark_verbs(text) == [], where
        assert H.pack_text(text) == (text, []), where
        # An opener's gate lifts the tags of ours too (`said_opener` has already): the
        # shipped Goodbye and Sleep lines start with one, and nothing else moves.
        assert H.pack_spoken(text) == (lift_every_action_tag(text), []), where
        assert H.pack_line(text, lambda a: True) == (text, [], []), where
    for name in sorted(SHIPPED):
        for item in P.review_pack({"items": [dict(kind=k, key=key, source_version=1, data=e["data"])
                                             for k, key, e in _items(P.shipped_items(_raw(name)))]}, {}):
            assert item["commands"] == [] and item["system_commands"] == [], item["id"]
            # The tick is the state's alone, as before (the shipped Goodbye's pattern is
            # over the pack cap, so as a pack item it is invalid and un-ticked: not ours).
            assert item["default"] is (item["state"] in P.DEFAULT_ACCEPT), item
            assert not any("command" in w for w in item["warnings"]), item["warnings"]
    from test_ext_say_tags import SHIPPED_INPUTS
    heard = {False: [], True: []}             # what each robot heard, with and without the gate
    for bypass in (False, True):
        if bypass:
            monkeypatch.setattr(H, "_cut_pack_marks", lambda text: (text, []))
            assert H.pack_spoken(UNPAIR + "Hi") == (UNPAIR + "Hi", []), "the gate is bypassed"
        else:
            assert H.pack_spoken(UNPAIR + "Hi") == ("Hi", ["start-systemunpair"])
        for name in sorted(SHIPPED):
            raw = _raw(name)
            for g in raw.get("globals", []):
                for speech in SHIPPED_INPUTS[g["name"]]:
                    brain = Brain()
                    app = shipped_app(name, brain, clock=lambda: 1_700_000_000)
                    reply = app.respond(Turn(robot=shipped_robot(SHIPPED[name]), speech=speech))
                    heard[bypass].append(((name, g["name"], speech), reply.text, reply.actions,
                                          reply.markup, reply.subscribe, brain.turns == []))
            for c in raw.get("conversations", []):
                for nickname in ("Sam", "Zoë", ""):
                    app = shipped_app(name, Brain(), rng=random.Random(5))
                    robot = ext_robot("d1", nickname=nickname, module_id=c["module_id"],
                                      content_id=c["content_id"])
                    replies = [app.respond(Turn(robot=robot, speech="", command="prompt"))
                               for _ in range(4)]
                    heard[bypass].append(((name, c["module_id"], nickname),
                                          [(r.text, r.actions, r.markup) for r in replies]))
    assert len(heard[False]) >= 60, "the walk exercised the shipped programs and openers"
    assert heard[False] == heard[True]
    assert any(row[1] for row in heard[False]), "the shipped lines were heard"


def _items(shipped):
    for full, entry in shipped.items():
        kind, key = full.split(":", 1)
        yield kind, key, entry


def test_a_trusted_python_handler_is_not_gated():
    """A registered Python handler is ours, not a pack's: what it sets reaches the robot
    as before, so a handler that writes a mark the gate would refuse from a pack is not
    changed by this (none of ours writes one)."""
    def handler(volley, session):
        volley.set_output(SCRIPTED + "Trusted.", None)
    module = dict(CHAT_MODULE, globals=[{"name": "Ours", "pattern": r"[\s\S]"}])
    app = app_with(module, chat=Brain(), global_handlers={"Ours": handler})
    reply = app.respond(Turn(robot=ext_robot(), speech="hi"))
    assert reply.text == SCRIPTED + "Trusted." and reply.actions == []


# --------------------------------------------------------------------------- #
# E. The floor and the planner never mint a system verb; the hello through the runtime (K7)
# --------------------------------------------------------------------------- #

def test_neither_the_floor_nor_the_planner_mints_a_system_verb():
    """`vocab.mark` is the one minting site, and nothing that performs a line calls it with
    a system verb: over a corpus of lines, through `annotate` and the seam's `perform`,
    every mark is expressive."""
    import importlib
    perform = importlib.import_module("markup").perform
    lines = ["Hi Sam! What do you want to talk about?", "Hmm, let me think about that.",
             "Goodbye! Sleep well.", "I'm so sorry.", "Wow! You did it!", "Oops.",
             "Can we pair up? Reset the game and update the score!", "Suspend disbelief."]
    for line in lines:
        for markup in (automarkup.annotate(line, turn_key="k"), perform(line, turn_key="k").markup):
            assert _clean(markup), (line, markup)
            assert set(vocab.mark_verbs(markup)) <= vocab.EXPRESSIVE_VERBS, (line, markup)
            assert vocab.validate_markup(markup) == []


def test_an_empty_prompt_over_the_shipped_starter_speaks_the_hello_through_the_runtime(tmp_path):
    """K7's deferred assertion: through the real runtime over the shipped `starter.json`,
    built as `config.build_content_app()` builds it, an empty `prompt` speaks the shipped
    Free Chat opener with the parent's child in it, performed by the floor, no model call,
    no action, and nothing on the wire a pack did not write."""
    seen = []

    def brain(messages):
        seen.append(messages)
        return "That sounds fun!"

    with open(os.path.join(REPO, "mqtt", "content_modules", "starter.json")) as fh:
        defaults = P.shipped_items(json.load(fh))
    app = CA.ContentApp(P.build_module(defaults, {}), brain, content_defaults=defaults,
                        memory=False, safety_classifier=False)
    out = _wire(app, "", module_id="FREE_CHAT", nickname="Sam", tmp_path=tmp_path)
    assert out["text"] == "Hi Sam! What do you want to talk about?", out
    assert seen == [], "the opener costs no model call"
    assert out["markup"] != out["text"] and "<mark" in out["markup"], "the hello is performed"
    assert _clean(out["markup"]) and vocab.validate_markup(out["markup"]) == []
    assert "Sam" in out["markup"]


# --------------------------------------------------------------------------- #
# F. `validate_markup` is linear in its text
# --------------------------------------------------------------------------- #

#: Shapes the catalogue's check once read in more than linear time, and two it already read
#: linearly, kept as pins: a unit, repeated to the size. The reviewer of #312 measured 7-14
#: ms per 8 KB of unclosed `data:{` openings; on origin/dev on the build host, 8, 16 and 32
#: KB took 14, 54 and 119 ms (`data:{`), 25, 101 and 388 ms (`<usel`), 58, 230 and 949 ms
#: (`<spurt`) and 0.35 s, 2.6 s and 20.4 s (`<usel genre="`), while a `<spurt spurt_id="`
#: run and a `<mark name=` run took under 0.2 ms at 32 KB.
_SHAPES = {
    "unclosed data:{": '<mark name="cmd:a,data:{',
    "a run of <usel": "<usel",
    "a run of <spurt": "<spurt",
    'a run of <usel genre="': '<usel genre="',
    'a run of <spurt spurt_id="': '<spurt spurt_id="',
    "a run of <mark name=": '<mark name="cmd:a',
    "one tag of genre= and no >": '<usel ' + 'genre="x" ' * 2,
}


def _repeated(unit, size):
    return (unit * (size // len(unit) + 1))[:size]


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_validate_markup_is_linear_in_its_text(shape):
    """Each pattern reads a tag from its opening to the next `<` or `>` and no further
    (`[^<>]`), so a run of openings costs each one its own gap: eight times the text, about
    eight times the time. Before, `data:{.*?}` read on to the end of the text from every
    unclosed opening and `[^>]*` from every `<usel` or `<spurt` opening (quadratic: four
    times longer per doubling), and a `<usel genre="` run read on from each `genre="` found
    on the way back (cubic: 0.35 s, 2.6 s, 20.4 s at 8, 16 and 32 KB, measured on the build
    host). The alarm catches a super-linear pass at 5 s: every shape that was super-linear
    took 119 ms or more at 32 KB, so 7.6 s or more at 256 KB. The bounds are what a loaded
    host clears by a wide margin (256 KB in 1.3-4.2 ms and 32 KB in 0.2-0.5 ms now, every
    shape, measured on the build host) and the old code could not; the ratio is read at 32
    and 256 KB so the stopwatch is in milliseconds, not in the noise of a runner's
    microseconds."""
    unit = _SHAPES[shape]
    took = {}
    try:
        with _hard_limit(5.0):
            for size in (32768, 262144):
                text = _repeated(unit, size)
                best = 1e9
                for _ in range(3):
                    started = time.perf_counter()
                    vocab.validate_markup(text)
                    best = min(best, time.perf_counter() - started)
                took[size] = best
    except _Stalled:
        pytest.fail(f"validate_markup still reading {shape} after 5 s")
    assert took[262144] < 0.25, f"{shape}: 256 KB took {took[262144] * 1000:.1f} ms"
    assert took[262144] < 40 * max(took[32768], 0.002), f"{shape}: {took}"


def test_validate_markup_still_reads_every_mark_this_appliance_mints_and_refuses_what_it_did():
    """The linear patterns read the same tags: every minted mark (nested data included)
    passes, an invented id in any slot is refused, a quoted `>` is still part of a usel's
    genre or a spurt's id (the whole-text read `robot_markup` relies on), and a mark whose
    data holds a `<` or `>` is not read as a mark (the gate drops it for its form first)."""
    for ok in (vocab.mood_mark(5, 2), vocab.tree_mark("Gesture_Point", "Bht_Sign_off"),
               vocab.icons_mark(["School", "Birthday"]), vocab.audio_mark(vocab.SFX_STINGER),
               vocab.usel("Hi?", "question"), '<spurt spurt_id="laugh"/>', vocab.break_mark(),
               vocab.mood_mark(1).replace('"/>', '" />'), UNPAIR):
        assert vocab.validate_markup(ok) == [], ok
    assert vocab.validate_markup('<mark name="cmd:zzz"/>') == ["verb=zzz"]
    assert vocab.validate_markup('<mark name="cmd:playback-mood,data:{+mood+:42,+intensity+:1}"/>') == ["mood=42"]
    assert vocab.validate_markup('<mark name="cmd:behaviour-tree,data:{+eventName+:+Gesture_We+}"/>') == [
        "eventName=Gesture_We"]
    assert vocab.validate_markup('<mark name="cmd:playaudio,data:{+SoundToPlay+:+nope+}"/>') == ["SoundToPlay=nope"]
    assert vocab.validate_markup('<usel genre="nope">Hi</usel>') == ["genre=nope"]
    assert vocab.validate_markup('<spurt spurt_id="nope"/>') == ["spurt_id=nope"]
    assert vocab.validate_markup('<spurt x" spurt_id="n>pe"/>Hi') == ["spurt_id=n>pe"]
    assert vocab.validate_markup('<usel x" genre="a>b">Hi</usel>') == ["genre=a>b"]
    assert vocab.validate_markup('<mark name="cmd:a,data:{">Hi') == []
    assert vocab._MARK_RE.fullmatch('<mark name="cmd:playback-mood,data:{+a+:+>+}"/>') is None
    assert vocab.validate_markup('<usel<usel genre="question">Hi') == []
    assert vocab.validate_markup("") == [] and vocab.validate_markup(None) == []
    # The pieces `robot_markup`'s shapes pin, read tag by tag, give what they gave.
    from test_ext_say_tags import MARKUPS as SAY_TAG_MARKUPS
    for markup, clean, dropped in SAY_TAG_MARKUPS.values():
        assert H.robot_markup(markup) == (clean, dropped), markup
