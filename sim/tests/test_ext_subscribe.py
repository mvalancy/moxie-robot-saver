"""The `subscribe` path end to end — a content extension that actually perceives.

`test_ext.py` proves the effect list and `test_ext_escapes.py` the closed catalog; this file
proves the subscription reaches the WIRE, merged alongside the supervisor's own vision
subscription rather than instead of it:

    {"subscribe": [event, …]}                     ext/  `_st_subscribe` / `_run_stmt`
      → {"kind": "subscribe", "events": […]}       ext.evaluate's effect list
      → volley.subscriptions                      content_app.apply_ext_effects
      → Reply.subscribe                           content_app.subscriptions_of
      → merged with the runtime's own list         moxie_runtime._merge_subscriptions
      → EventSubscription.active[] on the wire     wire.build_chat_response
      → the robot pushes the event back            (inbound section below)

The merge direction is the point: a pack may say "also tell me about this", never "only
this" — presence, greetings and launch cards depend on the runtime's latched list.

Design: `sandboxed-extensions.md` §4.5/§5.1; catalog: `vision.md` §1.1-1.2, §7.1.
"""
from helpers_ext import CHAT_MODULE as MODULE, app_with, robot
from helpers_runtime import CountingSynth, drive_turn, fresh_pool, make_runtime, seed_absent
from moxie_sdk import presence as P
from moxie_sdk.app import MoxieApp
from moxie_sdk import chat as C
from moxie_sdk.chat import make_openai_chat, make_openai_stream
from moxie_sdk.content import ext as E
from moxie_sdk.content import content_app as CA
from moxie_sdk.content.volley import Volley
from moxie_sdk.types import Turn, Reply, Action, ActionType, ResultCode
from moxie_sdk.wire import build_chat_response

QR = P.QR_EVENT                    # "eb-qr-event"
FOUND = P.FOUND_FACE               # "eb-found-face"


#: `MoxieGo`'s opening move: arm the scanner AND ask to be told what it sees (§5.1).
ARM_AND_WATCH = {
    "ext_format": 1,
    "capabilities": ["act.eb_enable_qr", "handled", "say", "subscribe"],
    "on": "turn.before",
    "rules": [{"do": [{"act": {"name": "eb_enable_qr", "args": ["true"]}},
                      {"subscribe": [QR]},
                      {"say": "Show me a card and I will read it!"},
                      {"handled": True}]}],
}

#: Subscribes and says nothing, so the model answers and the subscription must still get
#: out — the branch a naive implementation drops.
WATCH_ONLY = {
    "ext_format": 1,
    "capabilities": ["subscribe"],
    "on": "turn.before",
    "rules": [{"do": [{"subscribe": [QR]}]}],
}

SUB_GRANTS = (E.DEFAULT_GRANTS | {"subscribe", "act.eb_enable_qr"})


# --------------------------------------------------------------------------- #
# The vocabulary — one table, held equal to the recovered catalog
# --------------------------------------------------------------------------- #

def test_the_subscribable_events_are_exactly_the_recovered_vision_catalog():
    """`ext.SUBSCRIBE_EVENTS` == `presence.VISION_EVENTS`, order included. Two objects
    because `ext/` may not import `presence` (X7); this equality makes the copy safe."""
    assert E.SUBSCRIBE_EVENTS == P.VISION_EVENTS
    assert CA.robot_events() == frozenset(P.VISION_EVENTS)
    # One parent-facing sentence for the whole capability (§5.1).
    assert E.CAPABILITY_WORDS["subscribe"].startswith("Can ")


# --------------------------------------------------------------------------- #
# The chain, one link at a time
# --------------------------------------------------------------------------- #

def test_a_subscribe_effect_reaches_the_volley():
    """`apply_ext_effects` puts the events on the volley and reports how many."""
    v = Volley("")
    stats = CA.apply_ext_effects([{"kind": "subscribe", "events": [QR]}], volley=v)
    assert v.subscriptions == [QR]
    assert stats["subscribed"] == 1


def test_two_rules_asking_for_the_same_event_produce_one_entry():
    """`add_subscriptions` de-duplicates and preserves order: `active[]` never repeats."""
    v = Volley("")
    CA.apply_ext_effects([{"kind": "subscribe", "events": [QR, FOUND]},
                          {"kind": "subscribe", "events": [QR]}], volley=v)
    assert v.subscriptions == [QR, FOUND]
    assert CA.subscriptions_of(v) == [QR, FOUND]


def test_an_extension_adds_to_the_volley_and_never_replaces_it():
    """Merge layer one: an extension ADDS (`add_subscriptions`), never deletes a Python
    handler's subscription (`update_subscriptions` replaces)."""
    v = Volley("")
    v.update_subscriptions([FOUND])                # what a Python handler asked for
    CA.apply_ext_effects([{"kind": "subscribe", "events": [QR]}], volley=v)
    assert v.subscriptions == [FOUND, QR], "the handler's event must survive"


def test_the_subscription_becomes_a_reply_the_runtime_can_read():
    """`Reply.subscribe`; a Reply asking for nothing carries [] so other replies are unchanged."""
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
    """The model answers, and the robot must still be asked for the event."""
    app = app_with({**MODULE,
                    "conversations": [{**MODULE["conversations"][0],
                                       "extension": WATCH_ONLY}]},
                   ext_grants=SUB_GRANTS)
    reply = app.respond(Turn(robot=robot(), speech="hello"))
    assert reply.text == "the model answered"
    assert reply.subscribe == [QR]


def test_a_global_that_only_subscribes_does_not_fall_through_and_lose_it():
    """A global that produced only a subscription must not fall through to the conversation,
    whose fresh volley would drop it."""
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
    """Asks for a fixed event list on every reply: tests the runtime's merge, not the evaluator."""
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
    """Requirement 1 — the runtime's vision list survives in order, the pack's appended; and
    the latch must match what was actually sent, or the runtime never asks again."""
    rt, dev = make_runtime(_SubscribeApp(events=[QR]))
    resp = drive_turn(rt, dev, "hello")
    active = _active(resp)
    assert active[:len(P.VISION_EVENTS)] == list(P.VISION_EVENTS), \
        "the runtime's list comes first, in its own order"
    # The conjunction is the invariant: a pack-wins merge sets the latch but loses the events.
    assert rt._vision_subscribed.get(dev) == rt.robots[dev].module_id, \
        "the latch records the module it believes it subscribed for"


def test_a_pack_can_add_an_event_the_runtime_did_not_ask_for():
    """With the vision latch spent, the pack's request alone must still reach the wire —
    else `subscribe` works once per module."""
    rt, dev = make_runtime(_SubscribeApp(events=[QR]))
    first = drive_turn(rt, dev, "hello", event_id="e1")
    assert _active(first), "sanity: the first reply carries the runtime's own list"
    fresh_pool(rt)                                # drive_turn drained the old one
    second = drive_turn(rt, dev, "again", event_id="e2")
    assert _active(second) == [QR], \
        "the pack's request rides a reply the runtime had nothing of its own to send on"


def test_the_merge_is_a_pure_function_with_one_direction():
    """`_merge_subscriptions` alone. Empty is `None`: `build_chat_response`'s "no subscription"."""
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
    """Requirement 2 — asserted on the published JSON, not in-memory state. The first turn
    spends the vision latch on purpose: otherwise the runtime's own list hides a deleted
    pack contribution (S12/S13)."""
    rt, dev = make_runtime(_SubscribeApp(events=[QR]))
    drive_turn(rt, dev, "hello", event_id="e1")     # spends the runtime's own list
    fresh_pool(rt)
    resp = drive_turn(rt, dev, "again", event_id="e2")
    ra = resp["response_actions"]
    sub = ra[0]["event_subscription"]
    assert sub["clear"] is False, "additive on the robot: we never clear its subscriptions"
    assert sub["active"] == [QR], "the pack's request, and only it, on this reply"
    assert resp["response_action"]["event_subscription"] == sub, "legacy singular mirrored"
    # It really went through the transport.
    topic = f"/devices/{dev}/commands/remote_chat"
    assert (topic, resp) in rt.client.published


def test_a_subscription_rides_a_reply_that_already_carries_an_action():
    """Unlike the runtime's own, a pack's subscription may ride a reply with an action
    (`MoxieGo` opens with `act` + `subscribe`); both ride `response_actions[0]`."""
    act = Action(type=ActionType.EXECUTE, function="eb_enable_qr", args=["true"])
    resp = build_chat_response("e", "Show me a card!", actions=[act],
                               subscribe_events=[QR])
    assert resp["response_actions"] == [
        {"output_type": "GLOBAL_RESPONSE", "action": "execute",
         "function_id": "eb_enable_qr", "function_args": ["true"],
         "event_subscription": {"active": [QR], "clear": False}}]

    # Through the runtime: the action makes `mine` None, so the pack's list is the whole list.
    class _ActAndWatch(MoxieApp):
        name = "act-and-watch"

        def respond(self, turn):
            return Reply(text="Show me a card!", actions=[act], subscribe=[QR])

    rt, dev = make_runtime(_ActAndWatch())
    published = drive_turn(rt, dev, "hello")
    assert _active(published) == [QR]
    assert published["response_actions"][0]["function_id"] == "eb_enable_qr"


def test_a_reply_that_asks_for_nothing_carries_no_subscription_and_no_action():
    """Negative control: an app asking for nothing gets no subscription and no action —
    only the action-less `{output_type: GLOBAL_RESPONSE}` entry every reply carries
    (OpenMoxie's `create_response` envelope, `wire.build_chat_response`)."""
    class _Quiet(MoxieApp):
        name = "quiet"

        def respond(self, turn):
            return Reply(text="ok")

    rt, dev = make_runtime(_Quiet())
    drive_turn(rt, dev, "hello", event_id="e1")     # spends the runtime's own list
    fresh_pool(rt)
    resp = drive_turn(rt, dev, "again", event_id="e2")
    assert resp["response_actions"] == [{"output_type": "GLOBAL_RESPONSE"}], resp
    assert resp["response_action"] == {"output_type": "GLOBAL_RESPONSE"}, resp


# --------------------------------------------------------------------------- #
# The gates on a pack's request
# --------------------------------------------------------------------------- #

def test_vision_off_refuses_a_packs_request_too():
    """`MOXIE_VISION=0` outranks a pack."""
    rt, dev = make_runtime(_SubscribeApp(events=[QR]))
    rt.vision = False
    resp = drive_turn(rt, dev, "hello")
    assert _active(resp) == []
    assert rt._merge_subscriptions(dev, None, [QR]) is None


def test_an_unpermitted_robot_is_asked_for_nothing():
    """The pairing gate: an unpermitted robot is not asked to push what its camera sees."""
    rt, dev = make_runtime(_SubscribeApp(events=[QR]))
    rt.allow_unverified_bots = lambda: False
    assert not rt.is_permitted(dev)
    assert rt._merge_subscriptions(dev, None, [QR]) is None
    assert rt._vision_subscription(dev) is None


def test_the_runtime_drops_an_event_it_could_not_route():
    """Any `MoxieApp` may set `Reply.subscribe`, so the runtime bounds it to routable events."""
    rt, dev = make_runtime(_SubscribeApp(events=["eb-shell", QR]))
    assert rt._merge_subscriptions(dev, None, ["eb-shell", QR]) == [QR]
    assert rt._merge_subscriptions(dev, None, ["eb-shell"]) is None


def test_the_cap_is_structural_because_the_allowlist_is_shorter_than_it():
    """`MAX_SUBSCRIPTIONS` cannot bind while the catalog is shorter; notices if that changes."""
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
# A vision event is still diverted from the turn loop (never history, never `respond`);
# `_on_vision_turn` offers it to `MoxieApp.perceive`, which never costs a model call (§7.1).


class _WokenProbe(MoxieApp):
    """Records every event offered to `perceive` and answers a fixed line."""
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
    """Spend one turn so the request is recorded in `_pack_subscribed` (the inbound gate)."""
    resp = drive_turn(rt, dev, speech, event_id=event_id)
    assert rt._pack_subscribed.get(dev), "sanity: the request was not recorded"
    fresh_pool(rt)
    return resp


def test_a_subscribed_event_now_wakes_the_pack_that_asked_for_it():
    """The event is diverted before `respond` but offered to `perceive`, so G6's middle rule
    is reachable by a live robot's event."""
    app = _WokenProbe(events=[QR])
    rt, dev = make_runtime(app)
    _subscribed(rt, dev, app)
    resp = drive_turn(rt, dev, QR, input_vars={"$eb_qr_value": "GOnope"},
                      event_id="e-eye")
    assert app.perceived == [QR], "the pack asked for this event and must be woken by it"
    assert app.responded == ["hello"], \
        "…and the divert survived: the event never reached `respond`"
    assert resp["result"] == ResultCode.SUCCESS and resp["output"]["text"] == "I saw a card!"
    assert resp["event_id"] == "e-eye", "answered on the event's own event_id (§7.4)"
    assert resp["output"]["markup"] and "<mark" in resp["output"]["markup"], \
        "a pack's line is performed through the markup floor like any other"


def test_the_event_is_still_never_written_to_history():
    """§7.1: a woken pack answers, but the event is not something the child said."""
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
# G6 as a runnable pack: rule 1 arms on the opener (empty speech), rule 2 answers the event.
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
            # `delta_text` reads plain dicts, so the streaming double stays dependency-free.
            return iter([{"choices": [{"delta": {"content": self.text}}]}])
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=self.text))])


class _FakeOpenAI:
    """The `client=` seam (rule 9): no socket or key, but the REAL `make_openai_chat` around
    it, so `note_model_call` fires where it does in production."""

    def __init__(self, text="the model answered"):
        from types import SimpleNamespace
        self.chat = SimpleNamespace(completions=_FakeCompletions(text))


def _recording_brain():
    return make_openai_chat("http://gateway.invalid/v1", "", client=_FakeOpenAI())


def test_a_woken_pack_costs_zero_model_calls_and_a_counter_says_so():
    """A1 — a perception event never costs a model call (§7.1), measured by the recording
    counter; the control turn (counter moves) is what makes the zero meaningful."""
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

    # 4) A subscribed event with NO rule: routed to `respond`, the brain would answer a
    #    robot's eye. The counter is asserted first so it is the guard.
    fresh_pool(rt)
    with rt._presence_lock:
        rt._pack_subscribed[dev][FOUND] = "CHAT"
    before = C.model_calls()
    resp = drive_turn(rt, dev, FOUND, event_id="e4")
    assert C.model_calls() == before, \
        f"an unmatched perception event spent {C.model_calls() - before} model call(s)"
    assert resp["result"] == ResultCode.NOREPLY_ACK, resp


def test_the_counter_is_wired_to_the_real_gateway_seam():
    """Anti-vacuity for the counter: one call recorded per request, plain and streaming."""
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
    """A2 — a subscribed pack whose rules don't match leaves the greeting unchanged: one
    performed hello on the event's `event_id`, its TTS, and the `greeted_at` stamp."""
    C.reset_model_calls()
    app = app_with(GO_MODULE, chat=_recording_brain(), ext_grants=SUB_GRANTS)
    rt, dev = make_runtime(app, module_id="CHAT", content_id="default")
    rt.set_synthesizer(CountingSynth())
    rt.greet_after_s = 300.0
    # Subscribed to found-face: the pack IS woken, and still says nothing.
    drive_turn(rt, dev, "", event_id="e1")
    with rt._presence_lock:
        rt._pack_subscribed[dev][FOUND] = "CHAT"
    fresh_pool(rt)
    seed_absent(rt, dev, away_s=900.0)
    resp = drive_turn(rt, dev, FOUND, event_id="evt-eye")
    assert resp["result"] == ResultCode.SUCCESS, resp
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
    """`MOXIE_GREET_AFTER_S=0` stays off with a non-matching pack installed."""
    app = app_with(GO_MODULE, chat=_recording_brain(), ext_grants=SUB_GRANTS)
    rt, dev = make_runtime(app, module_id="CHAT", content_id="default")
    rt.greet_after_s = 0.0
    drive_turn(rt, dev, "", event_id="e1")
    with rt._presence_lock:
        rt._pack_subscribed[dev][FOUND] = "CHAT"
    fresh_pool(rt)
    seed_absent(rt, dev, away_s=9000.0)
    assert drive_turn(rt, dev, FOUND, event_id="e2")["result"] == ResultCode.NOREPLY_ACK


def test_an_app_that_never_heard_of_perception_is_untouched():
    """The base-class `perceive` returns None, so apps that did not opt in are untouched."""
    assert MoxieApp().perceive(Turn(robot=robot(), speech=QR)) is None
    rt, dev = make_runtime(_SubscribeApp(events=[QR]))
    drive_turn(rt, dev, "hello", event_id="e1")
    fresh_pool(rt)
    assert drive_turn(rt, dev, QR, event_id="e2")["result"] == ResultCode.NOREPLY_ACK


# --------------------------------------------------------------------------- #
# A3 + the gates — a pack is woken by what it asked for, and by nothing else
# --------------------------------------------------------------------------- #

def test_a_pack_is_not_woken_by_an_event_it_did_not_ask_for():
    """A3 — the evaluator must not run at all (not "run and match nothing")."""
    app = _WokenProbe(events=[QR])
    rt, dev = make_runtime(app)
    _subscribed(rt, dev, app)
    resp = drive_turn(rt, dev, FOUND, event_id="e-eye")
    assert app.perceived == [], "an event nobody asked for must not reach a pack"
    assert resp["result"] == ResultCode.NOREPLY_ACK, resp


def test_a_request_made_under_one_module_does_not_wake_the_next_one():
    """Events unsubscribe on module exit (RemoteModuleAPI), so the record is keyed on the module."""
    app = _WokenProbe(events=[QR])
    rt, dev = make_runtime(app)
    _subscribed(rt, dev, app)
    rt.robots[dev].module_id = "BEDTIME"
    assert drive_turn(rt, dev, QR, event_id="e2")["result"] == ResultCode.NOREPLY_ACK
    assert app.perceived == []


def test_a_module_exit_forgets_the_pack_request_with_the_vision_latch():
    """One invalidator clears both the vision latch and the pack record."""
    app = _WokenProbe(events=[QR])
    rt, dev = make_runtime(app)
    _subscribed(rt, dev, app)
    rt._end_conversation(dev, "module exit")
    assert dev not in rt._vision_subscribed and dev not in rt._pack_subscribed


def test_vision_off_refuses_to_wake_a_pack_too():
    """`MOXIE_VISION=0` on the way in: a pack armed before the switch is not woken after."""
    app = _WokenProbe(events=[QR])
    rt, dev = make_runtime(app)
    _subscribed(rt, dev, app)
    rt.vision = False
    assert drive_turn(rt, dev, QR, event_id="e2")["result"] == ResultCode.NOREPLY_ACK
    assert app.perceived == []


def test_an_unpermitted_robot_cannot_wake_a_pack():
    """The pairing gate on the way in."""
    app = _WokenProbe(events=[QR])
    rt, dev = make_runtime(app)
    _subscribed(rt, dev, app)
    rt.allow_unverified_bots = lambda: False
    assert not rt.is_permitted(dev)
    assert drive_turn(rt, dev, QR, event_id="e2")["result"] == ResultCode.NOREPLY_ACK
    assert app.perceived == []


def test_a_pack_that_raises_still_leaves_the_child_a_hello():
    """Fail-boring (§6.4): whatever `perceive` does, the greeting still runs."""
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
    assert resp["result"] == ResultCode.SUCCESS and "Sam" in resp["output"]["text"]


def test_a_pack_that_answers_with_nothing_falls_through_to_the_greeting():
    """An empty `Reply` (e.g. a memory-only rule) is not an answer; it must not eat the hello."""
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
    assert resp["result"] == ResultCode.SUCCESS and "Sam" in resp["output"]["text"]


def test_a_woken_pack_can_act_and_re_subscribe_on_the_same_reply():
    """`MoxieGo`'s loop (§5.1): read the card, re-arm, and renew on the event's own reply."""

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
