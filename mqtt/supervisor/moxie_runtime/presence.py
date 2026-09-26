"""Presence from the robot's own vision events: greetings, openers, event subscriptions."""
from __future__ import annotations
import time

from moxie_sdk.types import Turn, ResultCode
from moxie_sdk import presence as presence_seam
from moxie_sdk import launch_cards as cards_seam
from markup import make_markup


class PresenceMixin:
    # ---- presence: the robot's own eyes (audit BEYOND #9) ------------------------
    #
    # Moxie runs its vision ON-DEVICE and never sends pixels; what it can send is a
    # handful of semantic strings — `eb-found-face`, `eb-lost-target`, `eb-qr-event`,
    # `eb-dr-event`, `eb-br-event` — with no bounding box, no position, no identity
    # (docs/architecture/vision.md §1.1-1.2, :47-58). Two facts shape everything here:
    #
    #  1. **They are not their own topic.** A subscribed event is delivered to the brain
    #     as the `speech` of an ordinary `RemoteChatRequest` ("instead of the modules
    #     receiving something the user said, it receives a special event string like
    #     `eb-found-face`" — OpenMoxie `doc/RemoteModuleAPI.md` §Event Handling, MIT; the
    #     same shape content-and-conversation.md:385-390 shows for QR). So the ingest
    #     point is the chat router, and a reply to that request is not merely legal, it is
    #     REQUIRED: "the remote module must produce some response for this input to
    #     continue the interaction."
    #  2. **Nothing arrives until we ask.** The events are "discarded by the application
    #     stack unless the active module is specifically interested" — the brain opts in
    #     with `RemoteChatAction.EventSubscription{clear, active[]}`
    #     (remote-chat-protocol.md:103-106). `_vision_subscription` attaches that.
    #
    # Everything below is INFERRED from that recovered catalog. No physical robot has ever
    # sent us one of these events.

    def _presence_state(self, robot) -> dict:
        """This robot's raw presence record (`moxie_sdk.presence.new_state()` shape)."""
        st = robot.extra.get("presence")
        return st if isinstance(st, dict) else presence_seam.new_state()

    def _ingest_vision(self, device_id, robot, name, payload, now=None) -> list:
        """Fold one vision event into the robot's presence state; return its signals.

        The state lives on `RobotContext.extra["presence"]` — bounded, JSON-safe, and
        rebuilt (never mutated) by the pure helper, so the MQTT loop only ever swaps a
        reference. The app's `on_event` hook is called for every one of them, so a game
        or agent can react to perception without knowing the wire at all."""
        now = time.time() if now is None else now
        with self._presence_lock:
            state, signals = presence_seam.update_presence(
                self._presence_state(robot), name, payload, now)
            robot.extra["presence"] = state
        for sig in signals:
            detail = ""
            if sig.get("away_s") is not None:
                detail = f" after {presence_seam.human_duration(sig['away_s'])}"
            elif sig.get("present_s") is not None:
                detail = f" after {presence_seam.human_duration(sig['present_s'])}"
            elif sig.get("value"):
                detail = f": {str(sig['value'])[:32]}"
            self._note("vision", f"eye {sig['name']}{detail} ({device_id})")
            print(f"[runtime] eye {name} -> {sig['name']}{detail} on {device_id}",
                  flush=True)
        try:
            self.app_for(device_id).on_event(
                robot, name, dict(payload) if isinstance(payload, dict) else {})
        except Exception as e:
            print(f"[runtime] app.on_event error: {e}", flush=True)
        return signals

    def _on_vision_turn(self, device_id, robot, rcr, name):
        """A vision event that arrived as a chat turn — the protocol-faithful path.

        We answer the request the robot is waiting on, and we never spend a brain call on
        it: a sandboxed content pack that asked to perceive this event, a greeting, a 🎴
        launch card, or `NOREPLY_ACK` — ResultCode 6, "acknowledge only, no spoken line"
        (remote-chat-protocol.md:60), which is exactly the contract's field for "heard
        you, saying nothing".

        The pack is offered the event **first** and, if one of its rules answers, that is
        the whole reply — no greeting, no card, and no `greeted_at` stamp, because the
        pack took this event rather than sharing it. That ordering is a real decision: a
        pack subscribed to `eb-found-face` and matching every one of them displaces the
        greeting rule for as long as it is the active module, which is what "the pack owns
        the event it asked for" has to mean if it is to mean anything. When no rule
        matches, everything below runs unchanged.

        The card is the first thing in this appliance that *acts* on a perception event
        rather than merely noticing it. Honest ceiling: no physical Moxie has ever sent us
        an `eb-qr-event`, so this path is exercised only by the SIL robot and the browser
        SIM (docs/architecture/backlog/qr-launch-cards.md §7)."""
        event_id = rcr.get("event_id")
        backend = rcr.get("backend", "router")
        input_vars = rcr.get("input_vars") or {}
        signals = self._ingest_vision(device_id, robot, name, input_vars)
        # 👁️→🧬 **The inbound half of `subscribe`.** A pack that asked to perceive this
        # event gets it offered to its LOCAL evaluator before anything below runs — and
        # only its evaluator, so the paragraph above stays true word for word: no brain
        # call, no history, no utterance assessment. If a rule answers, that reply is the
        # turn and the presence handling below does not run; if none does, everything
        # below is byte-for-byte what it was before this line existed.
        if self._wake_subscribed_pack(device_id, robot, rcr, name, input_vars):
            return None
        # 🎴 A printed launch card. This is the ONLY place a scanned QR value is in scope
        # while a reply is being built, so the route lives here and nowhere else: a vision
        # event is intercepted before any brain sees it (`:2908` — never handed to a brain,
        # never written to history) and `MoxieApp.on_event` cannot shape a reply. The
        # decoder is pure and closed (`moxie_sdk/launch_cards.py`); everything it refuses —
        # an unknown module id, `<sleep>`, `<exit>`, `<launch_if_confirmed:…>`, a value with
        # no `GO` marker — arrives here as None and is simply a QR we noticed and ignored.
        card = cards_seam.decode_event(name, input_vars)
        greeting = self._greeting_for(device_id, robot, signals)
        if greeting is None and card is None:
            return self._publish_chat(device_id, event_id, backend, "", markup="",
                                      result=ResultCode.NOREPLY_ACK)
        # A card and a hello are independent, and a turn may carry both: a child who walks
        # back in holding a card gets one greeting and one launch on one reply, never two
        # replies and never a doubled hello.
        text, markup = greeting if greeting is not None else ("", "")
        if greeting is not None:
            self._note("chat", f"hello (unprompted): '{text[:40]}'")
            print(f"[runtime] 👋 {device_id} walked back in -> '{text}'", flush=True)
        if card is not None:
            self._note("vision", f"🎴 launch card -> {card.module_id}")
            print(f"[runtime] 🎴 {device_id} scanned a launch card -> {card.module_id}",
                  flush=True)
        # No invented line for a card on its own: the reply carries the launch and stays
        # silent, so a child never hears a decoding artefact.
        greet_scored = None
        if text:
            _, greet_scored = self._stage(text, turn_key=event_id, markup=markup)
        self._publish_chat(device_id, event_id, backend, text, markup,
                           actions=[card] if card is not None else None,
                           result=ResultCode.SUCCESS, scored=greet_scored)
        if text:
            self._maybe_synthesize(device_id, markup, event_id, chunk_num=0)
        return None

    def _wake_subscribed_pack(self, device_id, robot, rcr, name, input_vars) -> bool:
        """Offer a perceived event to the app that asked for it. True ⇔ it answered.

        **This closes the hole the outbound slice shipped with, and the constraint that
        makes it safe is not negotiable.** A subscribed event arrives as the `speech` of
        an ordinary `RemoteChatRequest` and `_on_remote_chat` diverts it here rather than
        into the turn loop, because a vision event *"is never assessed as a child's
        utterance, never enters history, **never costs a model call**"*
        (docs/architecture/vision.md §7.1). `eb-found-face` fires every time a child moves
        around the room; routing perception to a brain would turn presence into a billing
        event. So the event is offered to `MoxieApp.perceive`, whose `ContentApp`
        implementation runs the **sandboxed evaluator only** — pure, budgeted, offline —
        and never `respond`. The property is asserted in the suite from
        `moxie_sdk.chat.model_calls()`, a counter recorded immediately before the gateway
        request itself, rather than from a stub that stayed quiet.

        Four gates, and the first three are the outbound half's own gates read backwards
        so that the two directions cannot disagree:

        * **It must have asked.** `_pack_subscribed[device][event]` must equal this
          robot's current `module_id` — the record `_merge_subscriptions` wrote when it
          accepted the request. A pack is never woken by an event it did not ask for, and
          never by one it asked for under a module that has since exited (*"events are
          automatically unsubscribed when the module exits"*).
        * **`MOXIE_VISION=0`.** The operator's kill switch is above a content pack in both
          directions: if this appliance is not asking for perception, a pack is not
          answering it either.
        * **The pairing gate.** An unpermitted robot is served nothing, and "nothing"
          includes handing what its camera saw to a pack's program.
        * **The app must implement `perceive`.** The base class returns None, so every app
          that never heard of perception — `LLMApp`, `EchoApp`, `WebhookApp` — is
          unaffected, and so is a `ContentApp` whose active conversation has no extension.

        A `perceive` that raises is a non-event: logged once, False returned, and the
        greeting/card path below runs exactly as it always has. That is the same
        fail-boring rule the sandbox itself follows (brief §6.4) — a broken pack costs a
        child nothing, least of all a hello.
        """
        # Two `if`s rather than one `or`, so each gate can be deleted on its own by
        # `sim/tools/subscribe_mutation_check.py` — a guard nobody has watched fail is not
        # a guard, and a compound condition hides which half was load-bearing.
        if not self.vision:
            return False
        if not self.is_permitted(device_id):
            return False
        module = (getattr(robot, "module_id", None) or "")
        with self._presence_lock:
            asked = (self._pack_subscribed.get(device_id) or {}).get(name)
        if asked != module:
            return False
        app = self.app_for(device_id)
        perceive = getattr(app, "perceive", None)
        if not callable(perceive):
            return False
        # History is READ (a rule may look at `session.is_empty`) and never written: this
        # turn does not call `_remember`, so the event stays out of the transcript exactly
        # as §7.1 requires.
        turn = Turn(robot=robot, speech=name,
                    history=list(self.history.get(device_id, [])),
                    command=rcr.get("command", "prompt"), input_vars=input_vars,
                    presence=presence_seam.snapshot(self._presence_state(robot)))
        try:
            reply = perceive(turn)
        except Exception as e:                    # a broken pack must not cost the hello
            print(f"[runtime] app.perceive error: {e}", flush=True)
            return False
        if reply is None:
            return False
        text = (getattr(reply, "text", "") or "").strip()
        actions = list(getattr(reply, "actions", None) or [])
        subscribe = list(getattr(reply, "subscribe", None) or [])
        if not text and not actions and not subscribe:
            return False                          # answered with nothing: not an answer
        markup, scored = (getattr(reply, "markup", None), None)
        if text:
            markup, scored = self._stage(text, reply, turn_key=rcr.get("event_id"),
                                         chunk_index=0, markup=reply.markup)
        self._note("vision", f"🧬 a pack answered {name}: '{text[:40]}'")
        print(f"[runtime] 🧬 {device_id}: {name} woke a content pack -> "
              f"'{text[:60]}'", flush=True)
        self._publish_chat(device_id, rcr.get("event_id"),
                           rcr.get("backend", "router"), text, markup or "",
                           actions=actions or None,
                           end_turn=getattr(reply, "end_turn", False),
                           result=getattr(reply, "result_code", ResultCode.SUCCESS),
                           scored=scored, subscribe=subscribe or None)
        if text:
            self._maybe_synthesize(device_id, markup, rcr.get("event_id"), chunk_num=0)
        return True

    def _greeting_for(self, device_id, robot, signals):
        """`(text, markup)` if this robot has earned an unprompted hello, else None.

        The rule, and every gate on it:

        * an **`arrived`** signal whose `away_s` is at least `greet_after_s`
          (`MOXIE_GREET_AFTER_S`, default 300 s; **0 = off**). A first-ever sighting has
          `away_s = None` and never greets — Moxie does not shout at a stranger.
        * **once per absence** — `greeted_at` is stamped on the presence record and must
          predate the next `eb-lost-target` before another hello is possible.
        * **never over a turn** — a robot with a turn in flight gets the line *queued* for
          the start of the next turn instead (`_speak_opener`), so Moxie never talks over
          its own answer.
        * **never to an unpermitted robot** — the pairing gate already refuses their
          events upstream (`_serve_unpermitted`); this is the belt to that's braces.
        * **never in bedtime hours** — read-only use of `effective_config`.
        """
        if self.greet_after_s <= 0:
            return None
        arrived = next((s for s in signals if s.get("name") == "arrived"), None)
        if arrived is None:
            return None
        away = arrived.get("away_s")
        if away is None or away < self.greet_after_s:
            return None
        if not self.is_permitted(device_id):
            return None
        if self._in_bedtime(device_id):
            self._note("vision", f"hello suppressed (bedtime) for {device_id}")
            return None
        now = time.time()
        with self._presence_lock:
            state = self._presence_state(robot)
            greeted_at = state.get("greeted_at")
            lost_at = state.get("last_lost_at") or 0.0
            if greeted_at is not None and greeted_at >= lost_at:
                return None                       # already said hello for this absence
            text = presence_seam.pick_greeting(robot.child.nickname,
                                               self._last_greeting.get(device_id, ""))
            self._last_greeting[device_id] = text
            state = dict(state)
            state["greeted_at"] = now
            robot.extra["presence"] = state
            busy = device_id in self._busy
            if busy:
                self._pending_opener[device_id] = text
        if busy:
            self._note("vision", f"hello queued (turn in flight) for {device_id}")
            print(f"[runtime] 👋 queued opener for {device_id} (turn in flight)", flush=True)
            return None
        return text, make_markup(text, turn_key=f"greet|{device_id}|{now:.0f}",
                                 chunk_index=0)

    def _speak_opener(self, device_id, event_id, seq):
        """Deliver a queued hello as chunk 0 of the turn that is starting.

        Same wire shape a latency filler uses — `result=REPLY_PENDING` + `chunk_num=0`
        (RemoteChat.proto ResultCode 9 / field 22) — so the real answer follows as chunk 1
        and closes the sequence. Returns the text, or None if nothing was queued."""
        with self._presence_lock:
            text = self._pending_opener.pop(device_id, None)
        if not text or self._is_stale(device_id, seq):
            return None
        markup, scored = self._stage(text, turn_key=f"greet|{event_id}", chunk_index=0)
        self._note("chat", f"hello (queued): '{text[:40]}'")
        print(f"[runtime] 👋 delivering queued opener on {device_id}: '{text}'", flush=True)
        self._publish_chat(device_id, event_id, "router", text, markup,
                           result=ResultCode.REPLY_PENDING, chunk_num=0,
                           is_completed=False, scored=scored)
        self._maybe_synthesize(device_id, markup, event_id, chunk_num=0)
        return text

    def _in_bedtime(self, device_id, now=None) -> bool:
        """True when this robot's *effective* config puts it inside its bedtime window.

        Read-only use of `effective_config` (fleet ⊕ per-robot). The window is the pair of
        `"HH:MM"` local wall-clock strings the RobotCloudConfig already carries
        (`weekday_bedtime` / `weekend_bedtime`, cloud_config.py), weekday vs weekend by
        `datetime.weekday()` — the convention `WAKE_DAY_NAMES` fixes (0 = Monday). A
        window that wraps midnight (20:30-07:00, the normal case) is handled. No window
        configured -> never bedtime, which is the pre-presence behavior exactly."""
        import datetime
        from moxie_sdk.cloud_config import in_bedtime
        dt = (datetime.datetime.fromtimestamp(now) if now is not None
              else datetime.datetime.now())
        try:
            cfg = self.effective_config(device_id)
        except Exception:
            return False
        return in_bedtime(cfg, dt)

    def _vision_subscription(self, device_id, robot=None):
        """The `EventSubscription.active[]` list to attach to this response, or None.

        The robot discards its own vision events unless the *active module* subscribed,
        and "events are automatically unsubscribed when the module exits"
        (RemoteModuleAPI §Unsubscribing) — so the subscription is (re-)sent once per
        `(device, module_id)`, not once per process. `MOXIE_VISION=0` turns it off."""
        if not self.vision:
            return None
        robot = robot or self.robots.get(device_id)
        module = (getattr(robot, "module_id", None) or "") if robot else ""
        with self._presence_lock:
            if self._vision_subscribed.get(device_id) == module:
                return None
            if not self.is_permitted(device_id):
                return None
            self._vision_subscribed[device_id] = module
        self._note("vision", f"subscribed to vision events on {device_id}")
        print(f"[runtime] 👁️  subscribing {device_id} to "
              f"{', '.join(presence_seam.VISION_EVENTS)}", flush=True)
        return list(presence_seam.VISION_EVENTS)

    def _merge_subscriptions(self, device_id, mine, asked):
        """The supervisor's own `EventSubscription.active[]` list **⊕** what this reply
        asked for. Returns the merged list, or None when there is nothing to send.

        **The direction is the whole point, and getting it wrong is silent.** `mine` is
        the runtime's vision subscription — the events `presence.py` needs for
        arrived/left, the greeting rule and QR launch cards. `asked` is a *request* from
        the app layer, which in practice means a sandboxed content pack that declared
        `subscribe` (`content_app.subscriptions_of` → `Reply.subscribe`). A pack must be
        able to say *"also tell me about this"*; it must not be able to say *"only tell me
        about this"*, because the appliance's own behaviour is downstream of `mine` and a
        shorter list would switch it off. So `mine` is copied first and every entry of it
        survives, unconditionally, whatever the pack asked for.

        The failure this shape exists to prevent is worse than a missing event, which is
        why it is worth spelling out. `_vision_subscription` **latches**
        (`_vision_subscribed[device] = module`) at the moment it hands the list over: it
        believes the list has been sent. An implementation that let `asked` win — `asked
        or mine`, or a `dict` update in the other order — would set that latch and then
        publish a list without the vision events in it, and the runtime would never ask
        again for that `(device, module)`. Presence would go quiet with nothing logged.
        That is the cached-belief defect the playbook keeps re-finding, so the merge is
        one function with one direction and a test that fails if the direction flips.

        Two gates apply to `asked` and to neither `mine` (already gated in
        `_vision_subscription`) nor the merge:

        * **`MOXIE_VISION=0`.** The operator's kill switch is above a content pack. If
          this appliance is not asking the robot for perception events, a pack cannot ask
          on its behalf.
        * **The pairing gate.** An unpermitted robot is served nothing (`is_permitted`),
          and "nothing" includes a request to start pushing us what its camera sees.

        Names are bounded a third time against `presence_seam.VISION_EVENTS` — not for
        tidiness: `_on_remote_chat` / `_on_event` can only route an event from that
        catalog, so an event outside it would be a subscription this runtime could not
        act on if the robot honoured it.

        **Side effect, and it is the load-bearing one for the inbound half.** Every
        request this function *accepts* is written to `_pack_subscribed[device][event] =
        module`. That record is the only thing `_wake_subscribed_pack` consults, so the
        gates above are not merely advisory on the way out — an event refused here can
        never wake anything on the way back in, and *"a pack must not be woken by an event
        it never asked for"* is true because the ask and the wake read one dict written in
        one place.
        """
        merged = list(mine or [])
        if asked:
            if not self.vision:
                print(f"[runtime] 👁️  {device_id} asked for "
                      f"{', '.join(str(e) for e in asked)} but vision is off "
                      f"(MOXIE_VISION=0); refused", flush=True)
            elif not self.is_permitted(device_id):
                print(f"[runtime] 👁️  refusing an event subscription for unpermitted "
                      f"{device_id}", flush=True)
            else:
                # The module this request is made under. `_vision_subscribed` keys its own
                # latch the same way; see `_pack_subscribed` in `__init__` for why an
                # event asked for under module A must not wake module B.
                module = (getattr(self.robots.get(device_id), "module_id", None) or "")
                for event in asked:
                    name = str(event)
                    if name not in presence_seam.VISION_EVENTS:
                        print(f"[runtime] 👁️  {name!r} is not an event this appliance "
                              f"can route; dropped", flush=True)
                        continue
                    # 📥 **The record the INBOUND half reads.** Everything above this line
                    # is outbound — a request going out to the robot. This one line is
                    # what makes the return trip possible: `_wake_subscribed_pack` will
                    # hand a `eb-qr-event` to a pack's evaluator only if the pack is
                    # written here, under the module it is running now. It is recorded
                    # *outside* the `not in merged` branch below on purpose — an event the
                    # runtime already subscribes to for its own presence work adds nothing
                    # to `merged`, and a pack that asked for exactly that event would
                    # otherwise be unwakeable by it, which is the one case a reader would
                    # never suspect.
                    with self._presence_lock:
                        self._pack_subscribed.setdefault(device_id, {})[name] = module
                    if name not in merged:
                        merged.append(name)
                        self._note("vision", f"a content pack asked to be told about "
                                             f"{name} on {device_id}")
        return merged or None
