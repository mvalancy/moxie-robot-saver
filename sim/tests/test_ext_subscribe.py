"""The `subscribe` path end to end — a content extension that actually perceives.

`test_ext.py` proves the effect list and `test_ext_escapes.py` the closed catalog; this file
proves the subscription reaches the WIRE, merged alongside the supervisor's own vision
subscription rather than instead of it:

    {"subscribe": [event, …]}                     ext.py  `_st_subscribe` / `_run_stmt`
      → {"kind": "subscribe", "events": […]}       ext.evaluate's effect list
      → volley.subscriptions                      content_app.apply_ext_effects
      → Reply.subscribe                           content_app.subscriptions_of
      → merged with the runtime's own list         moxie_runtime._merge_subscriptions
      → EventSubscription.active[] on the wire     wire.build_chat_response
      → the robot pushes the event back            (inbound section below)

The merge direction is the point: a pack may say "also tell me about this", never "only
this". Presence, greetings and launch cards depend on the runtime's list, and
`_vision_subscription` latches `_vision_subscribed[device] = module` when it hands the list
over — so a pack-wins merge would silently switch them off for that `(device, module)`.

Design: `sandboxed-extensions.md` §4.5/§5.1; catalog: `vision.md` §1.1-1.2, §7.1.
"""
import os

from helpers_runtime import fresh_pool, seed_absent  # noqa: E402
from helpers_runtime import CountingSynth, drive_turn, make_runtime    # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

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
    """`ext.SUBSCRIBE_EVENTS` == `presence.VISION_EVENTS`, order included.

    Deliberately separate objects: `ext.py`'s import list is a security boundary (X7) and
    `presence.py` imports `os`. This equality is what makes the duplication safe, and it
    also means the appliance only asks for events `_on_remote_chat`/`_on_event` can route.
    """
    assert E.SUBSCRIBE_EVENTS == P.VISION_EVENTS
    assert CA.robot_events() == frozenset(P.VISION_EVENTS)
    # One parent-facing sentence for the whole capability (unlike `act.<name>`): "can
    # listen for things the robot notices" is one decision for a parent (§5.1).
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
    """Merge rule, layer one — within a volley. `update_subscriptions` REPLACES (a Python
    handler owns the volley), so `apply_ext_effects` must use `add_subscriptions`: an
    extension must never delete a handler's subscription."""
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
    """`WATCH_ONLY` neither speaks nor sets `handled`, so the model answers the child — and
    the robot must STILL be asked for the event."""
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


def _active(resp) -> list:
    """The `EventSubscription.active[]` list on a published response, or []."""
    for action in resp.get("response_actions") or []:
        sub = action.get("event_subscription") or {}
        if sub.get("active"):
            return list(sub["active"])
    return []


def test_a_packs_list_can_never_remove_what_the_runtime_put_there():
    """Requirement 1 — the runtime's full vision list survives, in its order, with the
    pack's new request appended; a pack that omits events cannot remove them.

    The latch assertion matters as much: `_vision_subscribed` must match what was actually
    sent, or the runtime would never ask again for that `(device, module)`.
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
    """Merging is not a no-op: with the vision latch already spent (runtime sends None),
    the pack's request alone must still reach the wire — else `subscribe` works once per
    module."""
    rt, dev = make_runtime(_SubscribeApp(events=[QR]))
    first = drive_turn(rt, dev, "hello", event_id="e1")
    assert _active(first), "sanity: the first reply carries the runtime's own list"
    fresh_pool(rt)                                # drive_turn drained the old one
    second = drive_turn(rt, dev, "again", event_id="e2")
    assert _active(second) == [QR], \
        "the pack's request rides a reply the runtime had nothing of its own to send on"


def test_the_merge_is_a_pure_function_with_one_direction():
    """`_merge_subscriptions` alone, four cases. Empty is `None`, not `[]`, because that is
    what `build_chat_response` reads as "no subscription on this reply"."""
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
    """Requirement 2 — asserted on the published `commands/remote_chat` JSON (including the
    legacy `response_action` mirror), not on in-memory state.

    The first turn spends the vision latch on purpose: every grantable event is in the
    runtime's own list, so without it this passes with the pack's contribution deleted
    (mutation rows S12/S13 stayed green in a draft that skipped it).
    """
    rt, dev = make_runtime(_SubscribeApp(events=[QR]))
    drive_turn(rt, dev, "hello", event_id="e1")     # spends the runtime's own list
    fresh_pool(rt)
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
    """The runtime's own subscription rides only an action-free closing reply; a PACK's must
    not inherit that restriction, since `MoxieGo` opens with an `act` + `subscribe` pair.
    Both ride `response_actions[0]`."""
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
    fresh_pool(rt)
    resp = drive_turn(rt, dev, "again", event_id="e2")
    assert "response_actions" not in resp and "response_action" not in resp


# --------------------------------------------------------------------------- #
# The gates on a pack's request
# --------------------------------------------------------------------------- #

def test_vision_off_refuses_a_packs_request_too():
    """`MOXIE_VISION=0` is the operator's kill switch and outranks a pack — an imported pack
    must not be a way around it."""
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
    """Third check on the event table, at the last step before the wire: `Reply.subscribe`
    is public and any `MoxieApp` may set it, so the runtime bounds it to routable events."""
    rt, dev = make_runtime(_SubscribeApp(events=["eb-shell", QR]))
    assert rt._merge_subscriptions(dev, None, ["eb-shell", QR]) == [QR]
    assert rt._merge_subscriptions(dev, None, ["eb-shell"]) is None


def test_the_cap_is_structural_because_the_allowlist_is_shorter_than_it():
    """`MAX_SUBSCRIPTIONS` (8) cannot bind while the catalog is six events; this notices the
    day the two numbers disagree."""
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
# `_on_remote_chat` still diverts a vision event away from the turn loop: never assessed as
# a child's utterance, never in history, never handed to `app.respond`. The one new branch
# inside `_on_vision_turn` offers it to `MoxieApp.perceive`, which for `ContentApp` runs
# only the sandboxed evaluator. `eb-found-face` fires constantly, so "never costs a model
# call" (vision.md §7.1) is asserted from a RECORDED counter below.


class _WokenProbe(MoxieApp):
    """Records every event offered to `perceive` and answers a fixed line — the assertion
    surface for "a pack is not woken by an event it did not ask for"."""
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
    """Spend one ordinary turn so the pack's request is accepted and recorded: the inbound
    gate reads `_pack_subscribed`, which only `_merge_subscriptions` writes."""
    resp = drive_turn(rt, dev, speech, event_id=event_id)
    assert rt._pack_subscribed.get(dev), "sanity: the request was not recorded"
    fresh_pool(rt)
    return resp


def test_a_subscribed_event_now_wakes_the_pack_that_asked_for_it():
    """A perception event still arrives as a `RemoteChatRequest`'s `speech` and is still
    diverted before `app.respond` (`responded` proves it), but `_on_vision_turn` now offers
    it to `perceive` first and a pack that asked for it answers. So G6's middle rule
    (`speech == "eb-qr-event"`) is reachable by a live robot's event.
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
# §8's G6 middle rule as a runnable pack: the first rule arms on the opener (empty speech),
# putting `eb-qr-event` into `_pack_subscribed`; the second is what a live event reaches.
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
    """A1 — a perception event never costs a model call (vision.md §7.1); a brain call per
    `eb-found-face` would make presence a billing event.

    Measured with `moxie_sdk.chat.note_model_call()`, which sits right before every request
    (retries and streams included), not with a raising stub that only proves one double was
    unused. The control turn — same brain, ordinary sentence, counter moves — is what makes
    the zero meaningful.
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
    fresh_pool(rt)
    drive_turn(rt, dev, "what is a dinosaur", event_id="e2")
    assert C.model_calls() == 1, "control: an ordinary turn DOES reach the brain"

    # 3) THE PROPERTY. The same brain, the same app, a subscribed event: the pack answers
    #    out of its own evaluator and the counter does not move.
    fresh_pool(rt)
    before = C.model_calls()
    resp = drive_turn(rt, dev, QR, input_vars={"$eb_qr_value": "GOdinosaur_quiz"},
                      event_id="e3")
    assert resp["output"]["text"] == "That card says dinosaur_quiz!", resp
    assert C.model_calls() == before, \
        f"a perception event spent {C.model_calls() - before} model call(s)"

    # 4) The costly case: a subscribed event with NO rule for it. Routing via `app.respond`
    #    instead of `perceive` is free in step 3 (the pack's rule answers), but here the
    #    brain would answer a robot's eye. The counter is asserted FIRST so it is the guard.
    fresh_pool(rt)
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

def test_a_pack_that_matches_nothing_leaves_the_greeting_exactly_as_it_was():
    """A2 — a subscribed pack whose rules don't match leaves the greeting byte-for-byte
    unchanged: `MOXIE_GREET_AFTER_S` honoured, one performed hello on the event's own
    `event_id`, a `CloudTTSResponse` for it, and the `greeted_at` stamp.
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
    fresh_pool(rt)
    seed_absent(rt, dev, away_s=900.0)
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
    fresh_pool(rt)
    seed_absent(rt, dev, away_s=9000.0)
    assert drive_turn(rt, dev, FOUND, event_id="e2")["result"] == "NOREPLY_ACK"


def test_an_app_that_never_heard_of_perception_is_untouched():
    """The base-class default. `LLMApp`, `EchoApp` and `WebhookApp` do not implement
    `perceive`, so the branch returns before it can do anything at all — which is what
    makes this slice invisible to every app but the one that opted in."""
    assert MoxieApp().perceive(Turn(robot=robot(), speech=QR)) is None
    rt, dev = make_runtime(_SubscribeApp(events=[QR]))
    drive_turn(rt, dev, "hello", event_id="e1")
    fresh_pool(rt)
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
    seed_absent(rt, dev, away_s=900.0)
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
    seed_absent(rt, dev, away_s=900.0)
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
