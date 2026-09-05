"""The `subscribe` path, end to end — a content extension that actually *perceives*.

`test_ext.py` proves the evaluator produces the right effect list; `test_ext_escapes.py`
proves a `subscribe` cannot be declared, granted or emitted outside the recovered event
catalog. This file proves the middle, and one thing neither of those can: that the
subscription reaches **the wire** and that it reaches it *alongside* the supervisor's own
vision subscription rather than instead of it.

The gap this closes is the third of the four `xfail(strict=True)` conformance rows.
`Volley.subscriptions` was **assigned** by `update_subscriptions` and **read by nothing**
— re-verified by grep on 2026-09-05, whose only other mentions in the tree were its own
declaration and the comment in `ext.py` saying it had no host. Meanwhile
`moxie_runtime._publish_chat` filled `RemoteChatAction.EventSubscription` from its own
vision bookkeeping. So a pack could declare `subscribe`, the parent could read *"can
listen for things the robot notices"* in the review, and nothing would happen.

The chain, and where each link is asserted below:

    {"subscribe": [event, …]}                     ext.py  `_st_subscribe` / `_run_stmt`
      → {"kind": "subscribe", "events": […]}       ext.evaluate's effect list
      → volley.subscriptions                      content_app.apply_ext_effects
      → Reply.subscribe                           content_app.subscriptions_of
      → merged with the runtime's own list         moxie_runtime._merge_subscriptions
      → EventSubscription.active[] on the wire     wire.build_chat_response
      → the robot starts pushing us the event      (unproven — see the last section)

**The direction of that merge is the point of this file.** A pack must be able to say
*"also tell me about this"* and must never be able to say *"only tell me about this"*: the
appliance's presence, greeting and launch-card behaviour are all downstream of the
runtime's own subscription, and a shorter list would switch them off. Worse, it would do
it silently — `_vision_subscription` latches `_vision_subscribed[device] = module` at the
moment it hands its list over, so a pack-wins merge sets the latch and then publishes a
list without the vision events in it, and the runtime never asks again for that
`(device, module)`. That is the cached-belief defect the integration playbook keeps
re-finding (rule 23), and `test_a_packs_list_can_never_remove_what_the_runtime_put_there`
below is the test that fails if the direction ever flips.

Design: `sandboxed-extensions.md` §4.5/§5.1. The event catalog and the subscription's own
contract: `vision.md` §1.1-1.2 and §7.1.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from helpers_runtime import CountingSynth, drive_turn, make_runtime    # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk import presence as P                                    # noqa: E402
from moxie_sdk.app import MoxieApp                                     # noqa: E402
from moxie_sdk import chat as C                                        # noqa: E402
from moxie_sdk.chat import make_openai_chat, make_openai_stream        # noqa: E402
from moxie_sdk.content import ext as E                                 # noqa: E402
from moxie_sdk.content import content_app as CA                        # noqa: E402
from moxie_sdk.content.content_app import ContentApp                   # noqa: E402
from moxie_sdk.content.module import load_modules                      # noqa: E402
from moxie_sdk.content.volley import Volley                            # noqa: E402
from moxie_sdk.types import Turn, Reply, RobotContext, ChildProfile    # noqa: E402
from moxie_sdk.wire import build_chat_response                         # noqa: E402

QR = P.QR_EVENT                    # "eb-qr-event"
FOUND = P.FOUND_FACE               # "eb-found-face"


def robot(device_id="robot-sub"):
    return RobotContext(device_id=device_id, module_id="", content_id="",
                        child=ChildProfile(nickname="Sam"))


#: `MoxieGo`'s opening move, which is the whole reason `subscribe` and `act` are described
#: in the brief as a pair: arm the scanner **and** ask to be told what it sees. *"A scanner
#: you cannot read from is pointless, and vice versa"* (§5.1).
ARM_AND_WATCH = {
    "ext_format": 1,
    "capabilities": ["act.eb_enable_qr", "handled", "say", "subscribe"],
    "on": "turn.before",
    "rules": [{"do": [{"act": {"name": "eb_enable_qr", "args": ["true"]}},
                      {"subscribe": [QR]},
                      {"say": "Show me a card and I will read it!"},
                      {"handled": True}]}],
}

#: The half that does **not** take the turn: it subscribes and says nothing, so the model
#: answers the child and the subscription must still get out. The `act` slice found this
#: branch was the one a naive implementation drops.
WATCH_ONLY = {
    "ext_format": 1,
    "capabilities": ["subscribe"],
    "on": "turn.before",
    "rules": [{"do": [{"subscribe": [QR]}]}],
}

MODULE = {"conversations": [{"name": "Chat", "module_id": "CHAT", "content_id": "default",
                             "prompt": "You are Moxie."}]}

SUB_GRANTS = (E.DEFAULT_GRANTS | {"subscribe", "act.eb_enable_qr"})


def app_with(module_json, chat=None, **kw):
    return ContentApp(load_modules(module_json), chat or (lambda m: "the model answered"),
                      default_module_id="CHAT", memory=False, safety_classifier=False,
                      **kw)


# --------------------------------------------------------------------------- #
# The vocabulary — one table, held equal to the recovered catalog
# --------------------------------------------------------------------------- #

def test_the_subscribable_events_are_exactly_the_recovered_vision_catalog():
    """`ext.SUBSCRIBE_EVENTS` == `presence.VISION_EVENTS`, asserted as an equality.

    The two lists are **deliberately** separate objects: X7 makes `ext.py`'s import list a
    security boundary (`math`, `re`, `unicodedata` and nothing else, asserted by parsing
    its own source), and `presence.py` imports `os` for its hysteresis knobs — so
    importing the tuple would trade a real invariant for a saved line. This test is what
    makes the duplication safe, and it is the only thing that does, so it asserts on
    *order* too: the tuple is what a subscription's `active[]` list is built from and the
    goldens compare byte for byte.

    `presence.VISION_EVENTS` is also the set `_on_remote_chat` / `_on_event` can route, so
    the equality says something stronger than "no drift": **this appliance only asks the
    robot for events it could actually act on if the robot sent them.**
    """
    assert E.SUBSCRIBE_EVENTS == P.VISION_EVENTS
    assert CA.robot_events() == frozenset(P.VISION_EVENTS)
    # Every one of them has the parent-facing sentence the review renders. There is one
    # sentence for the capability rather than one per event, which is a deliberate
    # difference from `act.<name>`: "can listen for things the robot notices" is one
    # decision a parent makes, where "can set a timer" and "can turn on the camera" are
    # two (§5.1).
    assert E.CAPABILITY_WORDS["subscribe"].startswith("Can ")


# --------------------------------------------------------------------------- #
# The chain, one link at a time
# --------------------------------------------------------------------------- #

def test_a_subscribe_effect_reaches_the_volley():
    """Link 3 — `apply_ext_effects` is what puts the events on the volley, and it reports
    how many it applied so the caller and the log can say so."""
    v = Volley("")
    stats = CA.apply_ext_effects([{"kind": "subscribe", "events": [QR]}], volley=v)
    assert v.subscriptions == [QR]
    assert stats["subscribed"] == 1


def test_two_rules_asking_for_the_same_event_produce_one_entry():
    """`add_subscriptions` de-duplicates and preserves order, so an `active[]` list never
    carries an event twice. Cosmetic on our side; not necessarily cosmetic on a robot's
    parser, and we have never seen a real one."""
    v = Volley("")
    CA.apply_ext_effects([{"kind": "subscribe", "events": [QR, FOUND]},
                          {"kind": "subscribe", "events": [QR]}], volley=v)
    assert v.subscriptions == [QR, FOUND]
    assert CA.subscriptions_of(v) == [QR, FOUND]


def test_an_extension_adds_to_the_volley_and_never_replaces_it():
    """The merge rule at its first layer — *within* one volley.

    `Volley.update_subscriptions` REPLACES, and it stays that way because a registered
    Python handler is our own code and owns the whole volley. `apply_ext_effects` must
    therefore call `add_subscriptions`, not that: an extension that could replace could
    delete a handler's subscription, and the *"merged, never replaced"* rule has to hold at
    every layer or it holds at none.
    """
    v = Volley("")
    v.update_subscriptions([FOUND])                # what a Python handler asked for
    CA.apply_ext_effects([{"kind": "subscribe", "events": [QR]}], volley=v)
    assert v.subscriptions == [FOUND, QR], "the handler's event must survive"


def test_the_subscription_becomes_a_reply_the_runtime_can_read():
    """Link 4 — `Reply.subscribe`. A `Reply` that asks for nothing carries an empty list,
    which is what keeps every other reply byte-identical on the wire."""
    app = app_with({**MODULE,
                    "conversations": [{**MODULE["conversations"][0],
                                       "extension": ARM_AND_WATCH}]},
                   ext_grants=SUB_GRANTS)
    reply = app.respond(Turn(robot=robot(), speech="hello"))
    assert reply.text == "Show me a card and I will read it!"
    assert reply.subscribe == [QR]
    assert [(a.function, a.args) for a in reply.actions] == [("eb_enable_qr", ["true"])]
    assert Reply(text="hi").subscribe == []


def test_a_turn_before_extension_that_only_subscribes_does_not_lose_it():
    """The branch a naive implementation drops on the floor — the `act` slice's lesson,
    applied to the other half of the pair.

    `WATCH_ONLY` neither speaks nor sets `handled`, so the model answers the child. The
    robot must **still** be asked for the event. Before this slice the volley's
    subscriptions were only ever read where a pack took the whole turn — which is to say,
    nowhere, because they were never read at all.
    """
    app = app_with({**MODULE,
                    "conversations": [{**MODULE["conversations"][0],
                                       "extension": WATCH_ONLY}]},
                   ext_grants=SUB_GRANTS)
    reply = app.respond(Turn(robot=robot(), speech="hello"))
    assert reply.text == "the model answered"
    assert reply.subscribe == [QR]


def test_a_global_that_only_subscribes_does_not_fall_through_and_lose_it():
    """The third location of the same gap. A matched global that produced *only* a
    subscription used to look like "nothing happened" and fall through to the
    conversation — which builds a FRESH volley, so the subscription died there."""
    watch_global = {**WATCH_ONLY, "on": "global"}
    app = app_with({**MODULE, "globals": [{"name": "Watch", "pattern": "keep an eye out",
                                           "extension": watch_global}]},
                   ext_grants=SUB_GRANTS)
    reply = app.respond(Turn(robot=robot(), speech="hey Moxie, keep an eye out"))
    assert reply.subscribe == [QR], "a global that only subscribed still produced something"


# --------------------------------------------------------------------------- #
# The merge — the direction, and the latch it protects
# --------------------------------------------------------------------------- #

class _SubscribeApp(MoxieApp):
    """An app that asks for a fixed event list on every reply. Stands in for a
    `ContentApp` running a pack, so the runtime tests are about the *merge* and not about
    the evaluator (which `test_ext.py` owns)."""
    name = "subscribe-probe"

    def __init__(self, events=(QR,), text="ok"):
        self.events = list(events)
        self.text = text

    def respond(self, turn):
        return Reply(text=self.text, subscribe=list(self.events))


def _fresh_pool(rt):
    """`drive_turn` shuts the worker pool down when it drains it, so a test that drives a
    SECOND turn through the same runtime needs a live one. (The idiom is
    `test_presence_runtime.py`'s; a second local copy beats importing across suites.)"""
    from concurrent.futures import ThreadPoolExecutor
    rt._pool = ThreadPoolExecutor(max_workers=4)
    return rt


def _active(resp) -> list:
    """The `EventSubscription.active[]` list on a published response, or []."""
    for action in resp.get("response_actions") or []:
        sub = action.get("event_subscription") or {}
        if sub.get("active"):
            return list(sub["active"])
    return []


def test_a_packs_list_can_never_remove_what_the_runtime_put_there():
    """**Requirement 1, and the reason this slice is a merge rather than a plumb.**

    The runtime is about to send its own vision subscription — all six recovered events,
    which presence, the greeting rule and launch cards all depend on. The pack asks for
    exactly one of them and, crucially, *omits* the rest. Every runtime entry must survive,
    in the runtime's own order, with the pack's request appended if it is new.

    The second assertion is the one that makes this more than a set-union test: the LATCH
    must be consistent with what was actually sent. `_vision_subscription` records
    `_vision_subscribed[device] = module` when it hands its list over. If a pack could win
    the merge, the latch would say "sent" while the wire carried a list without the vision
    events in it, and the runtime would never ask again for that `(device, module)` — eyes
    that never report, nothing logged. That is playbook rule 23's *"a cached belief about a
    moving thing"*, and it is why the merge is one function with one direction.
    """
    rt, dev = make_runtime(_SubscribeApp(events=[QR]))
    resp = drive_turn(rt, dev, "hello")
    active = _active(resp)
    for event in P.VISION_EVENTS:
        assert event in active, f"the runtime's own {event} must survive a pack's list"
    assert active[:len(P.VISION_EVENTS)] == list(P.VISION_EVENTS), \
        "the runtime's list comes first, in its own order"
    # The conjunction is the invariant, and it is the whole test: the latch now says
    # "sent for this module" AND the list that really went out contains the runtime's
    # events. A pack-wins merge would satisfy the first half and fail the second, which
    # is precisely the state nothing else in the suite would notice.
    assert rt._vision_subscribed.get(dev) == rt.robots[dev].module_id, \
        "the latch records the module it believes it subscribed for"
    assert set(P.VISION_EVENTS) <= set(active), \
        "…and what the latch claims was sent must actually have been sent"


def test_a_pack_can_add_an_event_the_runtime_did_not_ask_for():
    """The other direction of the same rule: merging is not a no-op.

    With the vision latch already set — the runtime has said its piece for this
    `(device, module)` and returns None from `_vision_subscription` — a pack's request is
    the *only* thing in the merged list, and it must still reach the wire. A gate that
    required the runtime's own list to be present would make `subscribe` work exactly once
    per module and then stop.
    """
    rt, dev = make_runtime(_SubscribeApp(events=[QR]))
    first = drive_turn(rt, dev, "hello", event_id="e1")
    assert _active(first), "sanity: the first reply carries the runtime's own list"
    _fresh_pool(rt)                                # drive_turn drained the old one
    second = drive_turn(rt, dev, "again", event_id="e2")
    assert _active(second) == [QR], \
        "the pack's request rides a reply the runtime had nothing of its own to send on"


def test_the_merge_is_a_pure_function_with_one_direction():
    """`_merge_subscriptions` on its own, so the direction is pinned without a turn.

    Four cases, and `None` rather than `[]` for the empty one because that is what
    `build_chat_response` treats as *"do not add a subscription to this reply"*.
    """
    rt, dev = make_runtime(_SubscribeApp())
    mine = list(P.VISION_EVENTS)
    assert rt._merge_subscriptions(dev, None, None) is None
    assert rt._merge_subscriptions(dev, mine, None) == mine
    assert rt._merge_subscriptions(dev, None, [QR]) == [QR]
    assert rt._merge_subscriptions(dev, mine, [QR]) == mine, \
        "an event already in the runtime's list adds nothing and reorders nothing"
    assert rt._merge_subscriptions(dev, [FOUND], [QR]) == [FOUND, QR]


# --------------------------------------------------------------------------- #
# On the wire — requirement 2: set-but-never-sent is this project's favourite bug
# --------------------------------------------------------------------------- #

def test_the_merged_list_is_on_the_published_event_subscription():
    """**Requirement 2.** Asserted against the published `commands/remote_chat` payload,
    not against any in-memory structure.

    This repo has been burned repeatedly by a value that was set and never sent — the
    readiness line, the roster ghost, the vision latch itself. `Volley.subscriptions` was
    the same shape of defect in its purest form: assigned, and read by nothing. So the
    assertion here is on the JSON that went to the transport, key for key, including the
    legacy singular `response_action` mirror `build_chat_response` keeps in sync.

    ⚠️ **The first turn is not decoration, and this test was WRONG without it.** Every
    grantable event is in the runtime's own list, so on turn one `QR in active` is
    satisfied by the *runtime's* subscription whatever the pack asked for — the assertion
    passes with the pack's contribution deleted entirely. The mutation harness is what
    said so: rows S12 (*"the merged list is computed and then not sent"*) and S13 (*"the
    turn loop drops `Reply.subscribe`"*) left this test **green** in its first draft. So
    the first turn spends the latch, and everything below is asserted on a reply the
    runtime had nothing of its own to say. A test that cannot fail for the reason it is
    named after is not a test, which is the whole argument for running the mutations.
    """
    rt, dev = make_runtime(_SubscribeApp(events=[QR]))
    drive_turn(rt, dev, "hello", event_id="e1")     # spends the runtime's own list
    _fresh_pool(rt)
    resp = drive_turn(rt, dev, "again", event_id="e2")
    ra = resp["response_actions"]
    sub = ra[0]["event_subscription"]
    assert sub["clear"] is False, "additive on the robot: we never clear its subscriptions"
    assert sub["active"] == [QR], "the pack's request, and only it, on this reply"
    assert resp["response_action"]["event_subscription"] == sub, "legacy singular mirrored"
    # And it really was the transport, not a return value: the same payload is in the
    # fake client's publish log under the robot's own command topic.
    topic = f"/devices/{dev}/commands/remote_chat"
    assert (topic, resp) in rt.client.published


def test_a_subscription_rides_a_reply_that_already_carries_an_action():
    """The runtime's own subscription is attached only to a plain, action-free closing
    reply, so that no reply already carrying a launch/exit changes shape. A **pack's**
    request must not inherit that restriction, because `MoxieGo`'s opening move is an
    `act` and a `subscribe` together — the exact pair §5.1 describes. A gate that dropped
    one whenever the other was present would make the pair unusable, which is the whole
    behaviour G6 ports.

    `build_chat_response` hangs the subscription on `response_actions[0]` whatever else
    that entry carries, so the `execute` and the `event_subscription` ride one action.
    """
    from moxie_sdk.types import Action, ActionType
    act = Action(type=ActionType.EXECUTE, function="eb_enable_qr", args=["true"])
    resp = build_chat_response("e", "Show me a card!", actions=[act],
                               subscribe_events=[QR])
    assert resp["response_actions"] == [
        {"output_type": "GLOBAL", "action": "execute", "module_id": None,
         "content_id": None, "function_id": "eb_enable_qr", "function_args": ["true"],
         "event_subscription": {"active": [QR], "clear": False}}]

    # …and through the runtime, where the action is what makes `mine` None (the vision
    # gate's `not actions`) and the pack's request is therefore the whole list.
    class _ActAndWatch(MoxieApp):
        name = "act-and-watch"

        def respond(self, turn):
            return Reply(text="Show me a card!", actions=[act], subscribe=[QR])

    rt, dev = make_runtime(_ActAndWatch())
    published = drive_turn(rt, dev, "hello")
    assert _active(published) == [QR]
    assert published["response_actions"][0]["function_id"] == "eb_enable_qr"


def test_a_reply_that_asks_for_nothing_is_unchanged_on_the_wire():
    """The negative control. With the vision latch already set and an app that asks for
    nothing, the reply carries no `response_actions` at all — so this slice is invisible to
    every app that does not use it."""
    class _Quiet(MoxieApp):
        name = "quiet"

        def respond(self, turn):
            return Reply(text="ok")

    rt, dev = make_runtime(_Quiet())
    drive_turn(rt, dev, "hello", event_id="e1")     # spends the runtime's own list
    _fresh_pool(rt)
    resp = drive_turn(rt, dev, "again", event_id="e2")
    assert "response_actions" not in resp and "response_action" not in resp


# --------------------------------------------------------------------------- #
# The gates on a pack's request
# --------------------------------------------------------------------------- #

def test_vision_off_refuses_a_packs_request_too():
    """`MOXIE_VISION=0` is the operator's kill switch, and it is above a content pack.

    If this appliance is not asking the robot for perception events, a pack cannot ask on
    its behalf — otherwise an imported pack would be a way around a switch somebody set
    deliberately.
    """
    rt, dev = make_runtime(_SubscribeApp(events=[QR]))
    rt.vision = False
    resp = drive_turn(rt, dev, "hello")
    assert _active(resp) == []
    assert rt._merge_subscriptions(dev, None, [QR]) is None


def test_an_unpermitted_robot_is_asked_for_nothing():
    """The pairing gate. An unpermitted robot is served no config and no brain
    (`is_permitted`), and "nothing" includes a request to start pushing us what its camera
    sees — which is the most physical thing on the list."""
    rt, dev = make_runtime(_SubscribeApp(events=[QR]))
    rt.allow_unverified_bots = lambda: False
    assert not rt.is_permitted(dev)
    assert rt._merge_subscriptions(dev, None, [QR]) is None
    # The runtime's own list is refused for the same robot by `_vision_subscription`, so
    # the merged answer is empty from both directions.
    assert rt._vision_subscription(dev) is None


def test_the_runtime_drops_an_event_it_could_not_route():
    """The third check on the same table, at the last function before the wire.

    `ext._st_subscribe` refuses an unknown event at load and `content_app.subscriptions_of`
    refuses it again at the host boundary — but `Reply.subscribe` is a public field on a
    public type, and any `MoxieApp` may set it. So the runtime bounds it once more against
    the events it can actually route when they arrive.
    """
    rt, dev = make_runtime(_SubscribeApp(events=["eb-shell", QR]))
    assert rt._merge_subscriptions(dev, None, ["eb-shell", QR]) == [QR]
    assert rt._merge_subscriptions(dev, None, ["eb-shell"]) is None


def test_the_cap_is_structural_because_the_allowlist_is_shorter_than_it():
    """§6.3 caps a program at `MAX_SUBSCRIPTIONS` events, counted across the whole effect
    list by `_over_output_caps`. Past the closed vocabulary that cap can never bind: the
    merged list is a subset of six names, and `MAX_SUBSCRIPTIONS` is eight. Recorded as a
    test rather than as a comment, because the day somebody widens the catalog to nine
    events this is the line that notices the two numbers now disagree."""
    assert len(E.SUBSCRIBE_EVENTS) <= E.MAX_SUBSCRIPTIONS
    v = Volley("")
    CA.apply_ext_effects([{"kind": "subscribe", "events": list(E.SUBSCRIBE_EVENTS)}],
                         volley=v)
    rt, dev = make_runtime(_SubscribeApp())
    merged = rt._merge_subscriptions(dev, list(P.VISION_EVENTS), CA.subscriptions_of(v))
    assert merged == list(P.VISION_EVENTS)
    assert len(merged) <= E.MAX_SUBSCRIPTIONS


# --------------------------------------------------------------------------- #
# The inbound half — a subscribed event WAKES the pack that asked for it
# --------------------------------------------------------------------------- #
#
# Until 2026-09-05 this section held the opposite assertion. `test_a_subscribed_event_
# still_never_reaches_the_pack_that_asked_for_it` pinned the divert in
# `moxie_runtime._on_remote_chat` as it stood — a pack could *ask* to perceive an event
# and could never be *woken* by one — and its docstring said in as many words that the day
# somebody routed events to the app layer it would go red and be rewritten. That day is
# this commit, so it is rewritten rather than deleted: same subject, inverted assertion,
# `seen == [QR]` where it used to say `seen == []`.
#
# **What did NOT change, and this is the whole reason the slice is safe.** The divert is
# still there and still diverts. A vision event is still never assessed as a child's
# utterance, never written to history and never handed to `app.respond`. What is new is
# one branch *inside* `_on_vision_turn` that offers the event to `MoxieApp.perceive`,
# whose `ContentApp` implementation runs the sandboxed evaluator and nothing else — no
# network, no brain. `eb-found-face` fires whenever a child moves around a room, so
# vision.md §7.1's *"never costs a model call"* is the property everything here is
# arranged around, and the first test below asserts it from a RECORDED counter rather
# than from a double that stayed quiet.


class _WokenProbe(MoxieApp):
    """Records every event offered to `perceive`, and answers a fixed line.

    Stands in for a `ContentApp` running a pack wherever the subject is the runtime's
    GATE rather than the evaluator: `perceived` is the assertion surface for *"a pack must
    not be woken by an event it did not ask for"*, which is a question about the gate and
    would be invisible if the only evidence were a missing line on the wire.
    """
    name = "woken-probe"

    def __init__(self, events=(QR,), text="I saw a card!"):
        self.events = list(events)
        self.text = text
        self.perceived: list = []
        self.responded: list = []

    def respond(self, turn):
        self.responded.append(turn.speech)
        return Reply(text="ok", subscribe=list(self.events))

    def perceive(self, turn):
        self.perceived.append(turn.speech)
        return Reply(text=self.text)


def _subscribed(rt, dev, app, *, speech="hello", event_id="e-sub"):
    """Spend one ordinary turn so the pack's request is accepted and RECORDED.

    Every test below needs this because the inbound gate reads `_pack_subscribed`, which
    only `_merge_subscriptions` writes — asking and being woken are deliberately the same
    dict, so there is no way to arrange the second without the first.
    """
    resp = drive_turn(rt, dev, speech, event_id=event_id)
    assert rt._pack_subscribed.get(dev), "sanity: the request was not recorded"
    _fresh_pool(rt)
    return resp


def test_a_subscribed_event_now_wakes_the_pack_that_asked_for_it():
    """⚠️ **The inversion.** This test used to be named `…still_never_reaches…` and to
    assert `seen == []`. It asserts the opposite now, on purpose and in one commit with
    the change that made it true.

    A perception event still arrives as the `speech` of an ordinary `RemoteChatRequest`
    and `_on_remote_chat` still diverts it to `_on_vision_turn` before `app.respond` can
    see it — `responded` below is the proof that the divert survived. What is new is that
    `_on_vision_turn` offers the event to `perceive` first, and a pack that asked for this
    event answers it.

    The concrete consequence for §8's G6: its middle rule, keyed on
    `speech == "eb-qr-event"`, is now reachable by a live robot's event and not only by
    the conformance golden. Its arming rules were always live.
    """
    app = _WokenProbe(events=[QR])
    rt, dev = make_runtime(app)
    _subscribed(rt, dev, app)
    resp = drive_turn(rt, dev, QR, input_vars={"$eb_qr_value": "GOnope"},
                      event_id="e-eye")
    assert app.perceived == [QR], "the pack asked for this event and must be woken by it"
    assert app.responded == ["hello"], \
        "…and the divert survived: the event never reached `respond`"
    assert resp["result"] == "SUCCESS" and resp["output"]["text"] == "I saw a card!"
    assert resp["event_id"] == "e-eye", "answered on the event's own event_id (§7.4)"
    assert resp["output"]["markup"] and "<mark" in resp["output"]["markup"], \
        "a pack's line is performed through the markup floor like any other"


def test_the_event_is_still_never_written_to_history():
    """§7.1's other half, which the inbound branch had every opportunity to break: a
    woken pack answers, and `eb-qr-event` is still not something the child said. Nothing
    calls `_remember` on this path, so the transcript the next real turn carries is
    unchanged."""
    app = _WokenProbe(events=[QR])
    rt, dev = make_runtime(app)
    _subscribed(rt, dev, app)
    before = list(rt.history.get(dev, []))
    drive_turn(rt, dev, QR, input_vars={"$eb_qr_value": "GOnope"}, event_id="e-eye")
    assert list(rt.history.get(dev, [])) == before, rt.history.get(dev)
    assert not any(QR in str(m) for m in rt.history.get(dev, []))


# --------------------------------------------------------------------------- #
# A1 — zero model calls, from a counter that RECORDS, not from a quiet stub
# --------------------------------------------------------------------------- #
#
# §8's G6 middle rule, hand-copied down to the `slice`, as a pack a runtime can run. The
# first rule arms on the opener (empty speech) and is what puts `eb-qr-event` into
# `_pack_subscribed`; the second is the one a live event now reaches.
GO_PACK = {
    "ext_format": 1,
    "capabilities": ["handled", "say", "subscribe"],
    "on": "turn.before",
    "rules": [
        {"when": {"==": [{"trim": [{"var": "speech"}]}, ""]},
         "do": [{"subscribe": [QR]},
                {"say": "Show me a card and I will read it!"},
                {"handled": True}]},
        {"when": {"and": [{"==": [{"var": "speech"}, QR]},
                          {"starts_with": [{"var": "input_vars.eb_qr_value"}, "GO"]}]},
         "do": [{"say": {"concat": ["That card says ",
                                    {"slice": [{"var": "input_vars.eb_qr_value"}, 2]},
                                    "!"]}},
                {"handled": True}]},
    ],
}

GO_MODULE = {"conversations": [{"name": "Go", "module_id": "CHAT",
                                "content_id": "default", "prompt": "You are Moxie.",
                                "extension": GO_PACK}]}


class _FakeCompletions:
    """`client.chat.completions` — the two attributes `make_openai_chat` reads."""

    def __init__(self, text="the model answered"):
        self.text = text

    def create(self, **kw):
        from types import SimpleNamespace
        if kw.get("stream"):
            # `delta_text` reads plain dicts as happily as the SDK's objects, so the
            # streaming double stays dependency-free (moxie_sdk/chat.py::delta_text).
            return iter([{"choices": [{"delta": {"content": self.text}}]}])
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=self.text))])


class _FakeOpenAI:
    """The `client=` seam of playbook rule 9: no socket, no key, no `openai` client — and
    crucially the REAL `make_openai_chat` around it, so `note_model_call` fires exactly
    where it fires in production."""

    def __init__(self, text="the model answered"):
        from types import SimpleNamespace
        self.chat = SimpleNamespace(completions=_FakeCompletions(text))


def _recording_brain():
    return make_openai_chat("http://gateway.invalid/v1", "", client=_FakeOpenAI())


def test_a_woken_pack_costs_zero_model_calls_and_a_counter_says_so():
    """**A1, the property the whole design rests on.**

    vision.md §7.1: a vision event *"is never assessed as a child's utterance, never
    enters history, **never costs a model call**"*. `eb-found-face` fires every time a
    child walks back into frame, so a brain call per perception event would turn presence
    into a billing event — which is exactly why `_on_remote_chat` diverts these away from
    the turn loop, and exactly what the inbound branch could have quietly undone by
    reaching for `app.respond`.

    **Why a counter and not a stub.** Handing the app a brain that raises when called
    proves only that *that* double was not called; it says nothing about a retry inside
    `call_with_backoff`, a stream opened elsewhere in the same turn, or a second brain the
    runtime built for this device. `moxie_sdk.chat.note_model_call()` sits immediately
    before the request itself — the same position and the same argument as
    `functions/api/_lib/limits.js::noteUpstreamCall()` on the edge, which
    `test_live_hosted_ears.py` asserts against for the same reason.

    The **control turn is the half that makes the zero mean something**: the same brain,
    the same runtime, an ordinary sentence, and the counter moves. A test whose counter
    can only ever read zero is not measuring anything.
    """
    C.reset_model_calls()
    app = app_with(GO_MODULE, chat=_recording_brain(), ext_grants=SUB_GRANTS)
    rt, dev = make_runtime(app, module_id="CHAT", content_id="default")

    # 1) the opener arms the pack: rule 1 subscribes, and `handled` means no model call.
    drive_turn(rt, dev, "", event_id="e1")
    assert rt._pack_subscribed.get(dev, {}).get(QR) == "CHAT"
    assert C.model_calls() == 0, "an armed pack has not spent anything yet"

    # 2) THE CONTROL. An ordinary sentence matches no rule, falls through to the
    #    conversation, and the counter records the call that really happened.
    _fresh_pool(rt)
    drive_turn(rt, dev, "what is a dinosaur", event_id="e2")
    assert C.model_calls() == 1, "control: an ordinary turn DOES reach the brain"

    # 3) THE PROPERTY. The same brain, the same app, a subscribed event: the pack answers
    #    out of its own evaluator and the counter does not move.
    _fresh_pool(rt)
    before = C.model_calls()
    resp = drive_turn(rt, dev, QR, input_vars={"$eb_qr_value": "GOdinosaur_quiz"},
                      event_id="e3")
    assert resp["output"]["text"] == "That card says dinosaur_quiz!", resp
    assert C.model_calls() == before, \
        f"a perception event spent {C.model_calls() - before} model call(s)"

    # 4) THE SHARP EDGE, and the mutation harness is what found it. Routing a perceived
    #    event through `app.respond` instead of `perceive` costs NOTHING in step 3 —
    #    the pack's own rule handles the turn and the model is never reached — so a
    #    counter checked only there would agree with the wrong implementation. The case
    #    that costs money is a subscribed event with **no rule for it**: the extension
    #    matches nothing, the conversation runs, and a brain answers a robot's eye. That
    #    is `eb-found-face` on any pack that subscribed to it for its own reasons, i.e.
    #    the exact event that fires every time a child walks back into frame. The counter
    #    assertion comes FIRST here so that it, and not the reply shape, is the guard.
    _fresh_pool(rt)
    with rt._presence_lock:
        rt._pack_subscribed[dev][FOUND] = "CHAT"
    before = C.model_calls()
    resp = drive_turn(rt, dev, FOUND, event_id="e4")
    assert C.model_calls() == before, \
        f"an unmatched perception event spent {C.model_calls() - before} model call(s)"
    assert resp["result"] == "NOREPLY_ACK", resp


def test_the_counter_is_wired_to_the_real_gateway_seam():
    """The counter's own anti-vacuity test. If `note_model_call` were dead code the test
    above would still be green, and would be proving nothing at all — so this drives the
    production function with the rule-9 `client=` seam and asserts one call is recorded
    per request attempt, on both the plain and the streaming seam."""
    C.reset_model_calls()
    chat = _recording_brain()
    assert chat([{"role": "user", "content": "hi"}]) == "the model answered"
    assert C.model_calls("chat") == 1 and C.model_calls() == 1
    stream = make_openai_stream("http://gateway.invalid/v1", "", client=_FakeOpenAI())
    list(stream([{"role": "user", "content": "hi"}]))
    assert C.model_calls("stream") == 1 and C.model_calls() == 2
    C.reset_model_calls()
    assert C.model_calls() == 0


# --------------------------------------------------------------------------- #
# A2 — with no rule to match, the presence behaviour is what it always was
# --------------------------------------------------------------------------- #

def _seed_absent(rt, dev, away_s):
    """Put this robot where it would be `away_s` seconds after a departure.

    Clock-RELATIVE, for the reason `test_presence_runtime.py`'s copy gives: the greeting
    is scored as an AGE, so a pinned epoch would make every robot look absent for years.
    A local copy rather than a cross-suite import, per `helpers_runtime.py`'s docstring.
    """
    import time
    now = time.time()
    state = P.new_state()
    state.update({"face_present": False, "announced": "left",
                  "last_seen_at": now - away_s - 30.0,
                  "present_since": now - away_s - 60.0,
                  "last_lost_at": now - away_s, "absent_since": now - away_s,
                  "faces_seen": 1, "events": 2})
    rt.robots[dev].extra["presence"] = state
    return state


def test_a_pack_that_matches_nothing_leaves_the_greeting_exactly_as_it_was():
    """**A2.** The regression that matters most, because the inbound branch sits directly
    upstream of the hello.

    A pack is installed, subscribed to `eb-found-face`, and its rules match only
    `eb-qr-event` — so `perceive` runs, finds nothing, and returns None. Everything below
    it must then be byte-for-byte the pre-slice behaviour: `MOXIE_GREET_AFTER_S` honoured,
    one performed hello on the event's own `event_id`, a `CloudTTSResponse` for the same
    id, and the `greeted_at` stamp that rate-limits the next one.
    """
    C.reset_model_calls()
    app = app_with(GO_MODULE, chat=_recording_brain(), ext_grants=SUB_GRANTS)
    rt, dev = make_runtime(app, module_id="CHAT", content_id="default")
    rt.set_synthesizer(CountingSynth())
    rt.greet_after_s = 300.0
    # Subscribe to found-face specifically: the pack IS woken, and still says nothing.
    drive_turn(rt, dev, "", event_id="e1")
    with rt._presence_lock:
        rt._pack_subscribed[dev][FOUND] = "CHAT"
    _fresh_pool(rt)
    _seed_absent(rt, dev, away_s=900.0)
    resp = drive_turn(rt, dev, FOUND, event_id="evt-eye")
    assert resp["result"] == "SUCCESS", resp
    text = resp["output"]["text"]
    assert "Sam" in text and len(text) < 70, text
    assert "<mark" in (resp["output"]["markup"] or ""), "the hello is still performed"
    # The LAST synthesis, not the first: the opener that armed the pack was spoken too.
    tts = rt.client.on(f"/devices/{dev}/commands/tts")
    assert tts and tts[-1]["event_id"] == "evt-eye", tts
    assert rt.robots[dev].extra["presence"].get("greeted_at"), \
        "the once-per-absence stamp still lands"
    assert C.model_calls() == 0, "and none of it cost a model call"


def test_the_greeting_switch_still_switches_it_off_with_a_pack_installed():
    """`MOXIE_GREET_AFTER_S=0` is off, and a pack that matches nothing cannot make Moxie
    speak by standing next to the switch."""
    app = app_with(GO_MODULE, chat=_recording_brain(), ext_grants=SUB_GRANTS)
    rt, dev = make_runtime(app, module_id="CHAT", content_id="default")
    rt.greet_after_s = 0.0
    drive_turn(rt, dev, "", event_id="e1")
    with rt._presence_lock:
        rt._pack_subscribed[dev][FOUND] = "CHAT"
    _fresh_pool(rt)
    _seed_absent(rt, dev, away_s=9000.0)
    assert drive_turn(rt, dev, FOUND, event_id="e2")["result"] == "NOREPLY_ACK"


def test_an_app_that_never_heard_of_perception_is_untouched():
    """The base-class default. `LLMApp`, `EchoApp` and `WebhookApp` do not implement
    `perceive`, so the branch returns before it can do anything at all — which is what
    makes this slice invisible to every app but the one that opted in."""
    assert MoxieApp().perceive(Turn(robot=robot(), speech=QR)) is None
    rt, dev = make_runtime(_SubscribeApp(events=[QR]))
    drive_turn(rt, dev, "hello", event_id="e1")
    _fresh_pool(rt)
    assert drive_turn(rt, dev, QR, event_id="e2")["result"] == "NOREPLY_ACK"


# --------------------------------------------------------------------------- #
# A3 + the gates — a pack is woken by what it asked for, and by nothing else
# --------------------------------------------------------------------------- #

def test_a_pack_is_not_woken_by_an_event_it_did_not_ask_for():
    """**A3.** Subscribed to `eb-qr-event`; a `eb-found-face` arrives. The evaluator must
    not run at all — not "run and match nothing", which would be a different and much
    weaker statement, and would leave a pack paying a step budget for every face."""
    app = _WokenProbe(events=[QR])
    rt, dev = make_runtime(app)
    _subscribed(rt, dev, app)
    resp = drive_turn(rt, dev, FOUND, event_id="e-eye")
    assert app.perceived == [], "an event nobody asked for must not reach a pack"
    assert resp["result"] == "NOREPLY_ACK", resp


def test_a_request_made_under_one_module_does_not_wake_the_next_one():
    """*"Events are automatically unsubscribed when the module exits"* (RemoteModuleAPI
    §Unsubscribing) — so the record is keyed on the module, exactly as the runtime's own
    `_vision_subscribed` latch is. A pack that asked while `CHAT` was running must not be
    woken by an event that arrives while `BEDTIME` is."""
    app = _WokenProbe(events=[QR])
    rt, dev = make_runtime(app)
    _subscribed(rt, dev, app)
    rt.robots[dev].module_id = "BEDTIME"
    assert drive_turn(rt, dev, QR, event_id="e2")["result"] == "NOREPLY_ACK"
    assert app.perceived == []


def test_a_module_exit_forgets_the_pack_request_with_the_vision_latch():
    """The two beliefs have one invalidator and one method, so they cannot drift: the
    thing that clears `_vision_subscribed` on a module exit clears this too."""
    app = _WokenProbe(events=[QR])
    rt, dev = make_runtime(app)
    _subscribed(rt, dev, app)
    rt._end_conversation(dev, "module exit")
    assert dev not in rt._vision_subscribed and dev not in rt._pack_subscribed


def test_vision_off_refuses_to_wake_a_pack_too():
    """`MOXIE_VISION=0` is above a content pack in BOTH directions. The outbound gate is
    already tested above; this is the same switch read on the way back in, and it is
    tested separately because a pack armed while vision was on would otherwise keep being
    woken after somebody turned it off."""
    app = _WokenProbe(events=[QR])
    rt, dev = make_runtime(app)
    _subscribed(rt, dev, app)
    rt.vision = False
    assert drive_turn(rt, dev, QR, event_id="e2")["result"] == "NOREPLY_ACK"
    assert app.perceived == []


def test_an_unpermitted_robot_cannot_wake_a_pack():
    """The pairing gate, on the way in. An unpermitted robot is served nothing, and
    "nothing" includes handing what its camera saw to somebody else's program."""
    app = _WokenProbe(events=[QR])
    rt, dev = make_runtime(app)
    _subscribed(rt, dev, app)
    rt.allow_unverified_bots = lambda: False
    assert not rt.is_permitted(dev)
    assert drive_turn(rt, dev, QR, event_id="e2")["result"] == "NOREPLY_ACK"
    assert app.perceived == []


def test_a_pack_that_raises_still_leaves_the_child_a_hello():
    """Fail-boring (§6.4), one layer up from the sandbox. `perceive` is app code and may
    do anything; whatever it does, the greeting rule underneath it must still run."""
    class _Broken(_WokenProbe):
        def perceive(self, turn):
            self.perceived.append(turn.speech)
            raise RuntimeError("the pack exploded")

    app = _Broken(events=[FOUND])
    rt, dev = make_runtime(app)
    _subscribed(rt, dev, app)
    rt.greet_after_s = 300.0
    _seed_absent(rt, dev, away_s=900.0)
    resp = drive_turn(rt, dev, FOUND, event_id="e2")
    assert app.perceived == [FOUND]
    assert resp["result"] == "SUCCESS" and "Sam" in resp["output"]["text"]


def test_a_pack_that_answers_with_nothing_falls_through_to_the_greeting():
    """`perceive` returning an empty `Reply` is not an answer. The distinction matters
    because a pack whose rule wrote only to memory produces exactly that, and swallowing
    the event there would silently delete the hello."""
    class _Silent(_WokenProbe):
        def perceive(self, turn):
            self.perceived.append(turn.speech)
            return Reply(text="")

    app = _Silent(events=[FOUND])
    rt, dev = make_runtime(app)
    _subscribed(rt, dev, app)
    rt.greet_after_s = 300.0
    _seed_absent(rt, dev, away_s=900.0)
    resp = drive_turn(rt, dev, FOUND, event_id="e2")
    assert app.perceived == [FOUND]
    assert resp["result"] == "SUCCESS" and "Sam" in resp["output"]["text"]


def test_a_woken_pack_can_act_and_re_subscribe_on_the_same_reply():
    """`MoxieGo`'s loop, closed. The card is read, the scanner is re-armed and the
    subscription is renewed on the reply to the event itself — the outbound and inbound
    halves meeting on one message, which is the shape §5.1 describes and the reason the
    pair was specified together."""
    from moxie_sdk.types import Action, ActionType

    class _ReArm(_WokenProbe):
        def perceive(self, turn):
            self.perceived.append(turn.speech)
            return Reply(text="Another one!",
                         actions=[Action(type=ActionType.EXECUTE,
                                         function="eb_enable_qr", args=["true"])],
                         subscribe=[QR])

    app = _ReArm(events=[QR])
    rt, dev = make_runtime(app)
    _subscribed(rt, dev, app)
    resp = drive_turn(rt, dev, QR, input_vars={"$eb_qr_value": "GOx"}, event_id="e2")
    assert resp["output"]["text"] == "Another one!"
    assert resp["response_actions"][0]["function_id"] == "eb_enable_qr"
    assert _active(resp) == [QR], "the renewal rides the same reply as the act"
