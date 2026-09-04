"""The `act` path, end to end — a content extension that actually makes the robot do a thing.

`test_ext.py` proves the evaluator produces the right **effect list**; `test_ext_escapes.py`
proves an `act` cannot be declared, granted or emitted outside a closed table. This file
proves the middle: that an effect list becomes a `RemoteChatAction` a robot can carry out,
and that it is spelled the way the recovered contract spells it.

The gap this closes is brief S5, *"the single most important scoping fact"* in
`docs/architecture/backlog/sandboxed-extensions.md`: `volley.execution_actions` was not on
the wire, so an `act` capability could not do anything and was refused at load rather than
shipped as a promise the appliance could not keep. Two changes closed it —
`wire.encode_action` learned `function_id` / `function_args` (#119, cited to
`RemoteChat.proto`:255-281), and `content_app.execution_actions_of` turns the effect into
an `execute` `Action`.

The chain, and where each link is asserted below:

    {"act": {"name, args}}                       ext.py  `_st_act` / `_run_stmt`
      → {"kind": "act", …}                       ext.evaluate's effect list
      → volley.execution_actions                 content_app.apply_ext_effects
      → Reply.actions [Action(EXECUTE, …)]       content_app.execution_actions_of
      → {"action": "execute", "function_id": …}  wire.encode_action
      → the robot does it                        sim/virtual_moxie.py

Design: `sandboxed-extensions.md` §4.5/§5.3. Wire shape and the closed-allowlist argument:
`qr-launch-cards.md` §P0-a/§P0-b.
"""
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.content import ext as E                              # noqa: E402
from moxie_sdk.content import content_app as CA                     # noqa: E402
from moxie_sdk.content.content_app import ContentApp                # noqa: E402
from moxie_sdk.content.module import load_modules                   # noqa: E402
from moxie_sdk.content.volley import Volley                         # noqa: E402
from moxie_sdk.types import Turn, RobotContext, ChildProfile, ActionType   # noqa: E402
from moxie_sdk.wire import build_chat_response                      # noqa: E402


def robot(device_id="robot-act"):
    return RobotContext(device_id=device_id, module_id="", content_id="",
                        child=ChildProfile(nickname="Sam"))


#: A global whose whole behaviour is one action and one line: *"set a timer"* arms the
#: robot's own timer and says so. The §4.1 worked example, shrunk to the one thing this
#: file is about.
TIMER = {
    "ext_format": 1,
    "capabilities": ["say", "handled", "act.eb_timer_request"],
    "on": "global",
    "rules": [{"do": [{"act": {"name": "eb_timer_request", "args": ["1", "300000"]}},
                      {"say": "Timer set."},
                      {"handled": True}]}],
}

#: The `turn.before` half — `MoxieGo`'s opening move, arming the QR scanner. It does **not**
#: speak and does **not** handle the turn, which is the case a naive implementation loses.
ARM_QR = {
    "ext_format": 1,
    "capabilities": ["act.eb_enable_qr"],
    "on": "turn.before",
    "rules": [{"do": [{"act": {"name": "eb_enable_qr", "args": ["true"]}}]}],
}

MODULE = {"conversations": [{"name": "Chat", "module_id": "CHAT", "content_id": "default",
                             "prompt": "You are Moxie."}]}

ACT_GRANTS = (E.DEFAULT_GRANTS | {"act.eb_timer_request", "act.eb_enable_qr"})


def app_with(module_json, chat=None, **kw):
    return ContentApp(load_modules(module_json), chat or (lambda m: "the model answered"),
                      default_module_id="CHAT", memory=False, safety_classifier=False,
                      **kw)


# --------------------------------------------------------------------------- #
# The chain, one link at a time
# --------------------------------------------------------------------------- #

def test_an_act_effect_reaches_the_volley_as_an_execution_action():
    """Link 3 — `apply_ext_effects` is what puts an action on the volley, and it puts the
    args on as **strings**, because both wire fields are `string` in the proto
    (`RemoteChat.proto`:271-273,:280) and `wire._arg_str` should never have to guess."""
    v = Volley("set a timer")
    stats = CA.apply_ext_effects(
        [{"kind": "act", "name": "eb_timer_request", "args": ["1", "300000"]}], volley=v)
    assert v.execution_actions == [{"name": "eb_timer_request",
                                    "args": ["1", "300000"]}]
    assert stats["acted"] == 1


def test_an_execution_action_becomes_an_execute_action_not_an_invented_verb():
    """Link 4 — and the one naming decision in this slice.

    Every robot function goes out as `execute` + `function_id`, which is what the recovered
    `RemoteChatAction.ActionID` actually defines (`execute` = 6, with `function_id` field 7
    and `repeated function_args` field 8 — `RemoteChat.proto`:255-281). `ActionType.ENABLE_QR`
    is **not** used, because `"enable_qr"` is not a verb in that enum at all; that member is a
    known naming defect owned by `qr-launch-cards.md` §P0-a and pinned, deliberately unfixed,
    by `test_actions_reach_the_robot.py::test_the_naming_defects_p0a_still_owns_are_pinned_here_not_fixed`.
    Routing *around* it is this slice's business; renaming it is not.
    """
    v = Volley("hi")
    v.add_execution_action("eb_enable_qr", ["true"])
    actions = CA.execution_actions_of(v)
    assert [(a.type, a.function, a.args) for a in actions] == [
        (ActionType.EXECUTE, "eb_enable_qr", ["true"])]
    assert all(a.type is not ActionType.ENABLE_QR for a in actions)


def test_the_wire_shape_is_the_briefs_own_worked_example():
    """Link 5 — asserted key for key against the JSON `qr-launch-cards.md` §P0-a prints.

    A **list** of args lands in `function_args` (field 8, `repeated string`), which is the
    type-decided mapping #119 introduced — a dict would have gone to `action_args` instead.
    This asserts the extension path produces the list form, so no third convention crept in.
    """
    v = Volley("hi")
    v.add_execution_action("eb_enable_qr", ["true"])
    resp = build_chat_response("e", "Show me a card!",
                               actions=CA.execution_actions_of(v))
    assert resp["response_actions"] == [
        {"output_type": "GLOBAL", "action": "execute", "module_id": None,
         "content_id": None, "function_id": "eb_enable_qr", "function_args": ["true"]}]


def test_a_global_extension_acts_and_speaks_in_one_reply():
    """The whole chain through `ContentApp.respond()` — the socket brief S1 describes,
    filled by a program instead of by Python, now producing an action as well as a line.

    And no model call: `handled` suppressed it, which is the point of a global.
    """
    calls = []
    app = app_with({**MODULE, "globals": [{"name": "Timer", "pattern": "set a timer",
                                           "extension": TIMER}]},
                   chat=lambda m: calls.append(m) or "the model answered",
                   ext_grants=ACT_GRANTS)
    reply = app.respond(Turn(robot=robot(), speech="hey Moxie, set a timer"))
    assert reply.text == "Timer set."
    assert [(a.type, a.function, a.args) for a in reply.actions] == [
        (ActionType.EXECUTE, "eb_timer_request", ["1", "300000"])]
    assert calls == [], "a handled global must not cost a model call"


def test_a_turn_before_extension_that_only_acts_does_not_lose_its_action():
    """The branch a naive implementation drops on the floor.

    `ARM_QR` neither speaks nor sets `handled`, so the model answers the child — and the
    robot must **still** be told to arm its scanner. Before this slice `_reply_from_volley`
    was only reached when a pack took the whole turn; an action that rode alongside a model
    answer had nowhere to go.
    """
    app = app_with({**MODULE,
                    "conversations": [{**MODULE["conversations"][0],
                                       "extension": ARM_QR}]},
                   ext_grants=ACT_GRANTS)
    reply = app.respond(Turn(robot=robot(), speech="hello"))
    assert reply.text == "the model answered", reply
    assert [(a.function, a.args) for a in reply.actions] == [("eb_enable_qr", ["true"])]


def test_a_turn_before_extension_that_acts_and_handles_answers_the_turn():
    """The other branch: acting *is* handling. A rule that answers the turn by arming the
    scanner rather than by speaking has handled it, and the model must not run."""
    handling = {**ARM_QR, "capabilities": ["act.eb_enable_qr", "handled"],
                "rules": [{"do": [{"act": {"name": "eb_enable_qr", "args": ["true"]}},
                                  {"handled": True}]}]}
    calls = []
    app = app_with({**MODULE,
                    "conversations": [{**MODULE["conversations"][0],
                                       "extension": handling}]},
                   chat=lambda m: calls.append(m) or "the model answered",
                   ext_grants=ACT_GRANTS)
    reply = app.respond(Turn(robot=robot(), speech="hello"))
    assert calls == [], "a handled turn.before must not cost a model call"
    assert [(a.function, a.args) for a in reply.actions] == [("eb_enable_qr", ["true"])]


# --------------------------------------------------------------------------- #
# The bound, at the seam a string becomes a `function_id`
# --------------------------------------------------------------------------- #

def test_the_nameable_functions_are_exactly_the_ones_with_parent_facing_words():
    """The bound, stated as an equality rather than as two lists that could drift.

    `ext.ACTION_WORDS` is simultaneously the allowlist of robot functions and the source of
    the sentence a parent reads before granting one — so a function nobody wrote English
    for cannot be declared, cannot be granted, and cannot be emitted. That is the same
    argument `qr-launch-cards.md` §P0-b makes for the launch-card catalogue: *"the catalog
    is a closed allowlist, and this is a safety property, not tidiness."*
    """
    assert CA.robot_functions() == frozenset(E.ACTION_WORDS)
    for name in CA.robot_functions():
        assert E.ACTION_WORDS[name].startswith("Can "), name
    # A pack cannot put a name of its own choosing on the wire, at either gate.
    for bogus in ("eb_shell", "eb_timer_request ", "eb_enable_qr;rm", "../eb_wake"):
        assert bogus not in CA.robot_functions()
        v = Volley("hi")
        v.add_execution_action(bogus, ["x"])
        assert CA.execution_actions_of(v) == []


@pytest.mark.parametrize("caps,grants,why", [
    (["say", "handled"], ACT_GRANTS, "used but not declared"),
    (["say", "handled", "act.eb_timer_request"], E.DEFAULT_GRANTS, "declared but not granted"),
    (["say", "handled", "act.eb_wake"], ACT_GRANTS, "declared the wrong one"),
])
def test_an_act_that_is_not_declared_and_granted_fails_at_load_not_at_runtime(caps, grants, why):
    """§4.2's *"absent, not refused, when not granted"*, for `act` specifically.

    Each of these is a **load** refusal, so the program never runs and the turn is never at
    risk: the child gets the model's answer and the robot is told nothing. A runtime refusal
    would mean a pack could get half its effects applied, which §4.5 forbids.
    """
    e = {**TIMER, "capabilities": caps}
    assert E.validate(e, grants=grants), why
    r = E.evaluate(e, {"speech": "", "entities": [], "input_vars": {}, "scratch": {},
                       "child": {}, "memory": {}, "session": {}, "presence": {}},
                   grants=grants)
    assert not r.ok and r.effects == [], why

    app = app_with({**MODULE, "globals": [{"name": "Timer", "pattern": "set a timer",
                                           "extension": e}]}, ext_grants=grants)
    reply = app.respond(Turn(robot=robot(), speech="set a timer"))
    assert reply.text == "the model answered", why
    assert reply.actions == [], why


def test_four_actions_is_the_cap_and_the_fifth_applies_nothing():
    """§6.3's output cap, on the path that now reaches a robot. Over the cap the whole
    effect list is discarded — not the prefix that fitted (§4.5) — so a pack cannot flood a
    robot with execution actions by writing a fifth statement."""
    stmt = {"act": {"name": "eb_wake", "args": []}}
    grants = E.DEFAULT_GRANTS | {"act.eb_wake"}
    facts = {"speech": "", "entities": [], "input_vars": {}, "scratch": {},
             "child": {}, "memory": {}, "session": {}, "presence": {}}
    ok = {"ext_format": 1, "capabilities": ["act.eb_wake"], "on": "global",
          "rules": [{"do": [stmt] * E.MAX_ACTIONS}]}
    r = E.evaluate(ok, facts, grants=grants)
    assert r.ok and len(r.effects) == E.MAX_ACTIONS

    over = {**ok, "rules": [{"do": [stmt] * (E.MAX_ACTIONS + 1)}]}
    r = E.evaluate(over, facts, grants=grants)
    assert not r.ok and r.breach == "output" and r.effects == []
    v = Volley("hi")
    CA.apply_ext_effects(r.effects, volley=v)
    assert v.execution_actions == []
