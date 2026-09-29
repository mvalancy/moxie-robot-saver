"""The `act` path end to end — a content extension that makes the robot do a thing.

`test_ext.py` proves the effect list, `test_ext_escapes.py` the closed table; this proves the
effect list becomes a `RemoteChatAction` spelled the way the recovered contract spells it
(`wire.encode_action` carries `function_id`/`function_args`, RemoteChat.proto:255-281):

    {"act": {"name, args}}                       ext/  `_st_act` / `_run_stmt`
      → {"kind": "act", …}                       ext.evaluate's effect list
      → volley.execution_actions                 content_app.apply_ext_effects
      → Reply.actions [Action(EXECUTE, …)]       content_app.execution_actions_of
      → {"action": "execute", "function_id": …}  wire.encode_action
      → the robot does it                        sim/virtual_moxie.py

Design: `sandboxed-extensions.md` §4.5/§5.1; wire shape: `qr-launch-cards.md` §P0-a/§P0-b.
"""
import pytest

from helpers_ext import CHAT_MODULE as MODULE, app_with, robot
from moxie_sdk.content import ext as E
from moxie_sdk.content import content_app as CA
from moxie_sdk.content.volley import Volley
from moxie_sdk.types import Turn, ActionType

#: §4.1's worked example, shrunk: "set a timer" arms the robot's timer and says so.
TIMER = {
    "ext_format": 1,
    "capabilities": ["say", "handled", "act.eb_timer_request"],
    "on": "global",
    "rules": [{"do": [{"act": {"name": "eb_timer_request", "args": ["1", "300000"]}},
                      {"say": "Timer set."},
                      {"handled": True}]}],
}

#: `MoxieGo`'s opening move: arm the QR scanner without speaking or handling the turn —
#: the case a naive implementation loses.
ARM_QR = {
    "ext_format": 1,
    "capabilities": ["act.eb_enable_qr"],
    "on": "turn.before",
    "rules": [{"do": [{"act": {"name": "eb_enable_qr", "args": ["true"]}}]}],
}

ACT_GRANTS = (E.DEFAULT_GRANTS | {"act.eb_timer_request", "act.eb_enable_qr"})
EMPTY_FACTS = {"speech": "", "entities": [], "input_vars": {}, "scratch": {},
               "child": {}, "memory": {}, "session": {}, "presence": {}}


# --------------------------------------------------------------------------- #
# The chain, one link at a time
# --------------------------------------------------------------------------- #

def test_an_act_effect_reaches_the_volley_as_an_execution_action():
    """`apply_ext_effects` puts the action on the volley with args as strings — both wire
    fields are `string` in RemoteChat.proto, so `wire._arg_str` never has to guess."""
    v = Volley("set a timer")
    stats = CA.apply_ext_effects(
        [{"kind": "act", "name": "eb_timer_request", "args": ["1", "300000"]}], volley=v)
    assert v.execution_actions == [{"name": "eb_timer_request",
                                    "args": ["1", "300000"]}]
    assert stats["acted"] == 1


def test_a_global_extension_acts_and_speaks_in_one_reply():
    """Through `ContentApp.respond()`: one action and one line; `handled` = no model call."""
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
    """`ARM_QR` neither speaks nor handles, so the model answers — and the action must
    still ride along with that answer."""
    app = app_with({**MODULE,
                    "conversations": [{**MODULE["conversations"][0],
                                       "extension": ARM_QR}]},
                   ext_grants=ACT_GRANTS)
    reply = app.respond(Turn(robot=robot(), speech="hello"))
    assert reply.text == "the model answered", reply
    assert [(a.function, a.args) for a in reply.actions] == [("eb_enable_qr", ["true"])]


def test_a_turn_before_extension_that_acts_and_handles_answers_the_turn():
    """A rule that answers the turn by acting instead of speaking has handled it."""
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

@pytest.mark.parametrize("caps,grants,why", [
    (["say", "handled"], ACT_GRANTS, "used but not declared"),
    (["say", "handled", "act.eb_timer_request"], E.DEFAULT_GRANTS, "declared but not granted"),
    (["say", "handled", "act.eb_wake"], ACT_GRANTS, "declared the wrong one"),
])
def test_an_act_that_is_not_declared_and_granted_fails_at_load_not_at_runtime(caps, grants, why):
    """A LOAD refusal (§4.2): the program never runs and the child gets the model's answer;
    a runtime refusal could half-apply effects (§4.5)."""
    e = {**TIMER, "capabilities": caps}
    assert E.validate(e, grants=grants), why
    r = E.evaluate(e, EMPTY_FACTS, grants=grants)
    assert not r.ok and r.effects == [], why

    app = app_with({**MODULE, "globals": [{"name": "Timer", "pattern": "set a timer",
                                           "extension": e}]}, ext_grants=grants)
    reply = app.respond(Turn(robot=robot(), speech="set a timer"))
    assert reply.text == "the model answered", why
    assert reply.actions == [], why


def test_four_actions_is_the_cap_and_the_fifth_applies_nothing():
    """§6.3: over the cap the WHOLE effect list is discarded, not the prefix that fitted
    (§4.5), so a pack cannot flood a robot with actions."""
    stmt = {"act": {"name": "eb_wake", "args": []}}
    grants = E.DEFAULT_GRANTS | {"act.eb_wake"}
    facts = EMPTY_FACTS
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
