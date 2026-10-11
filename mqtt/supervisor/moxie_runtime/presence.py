"""Presence from the robot's own vision events: greetings, openers, event subscriptions."""
from __future__ import annotations
import time

from moxie_sdk.types import Turn, ResultCode
from moxie_sdk import presence as presence_seam
from moxie_sdk import launch_cards as cards_seam
from moxie_sdk import safety as safety_seam
from moxie_sdk.cloud_config import GENERIC_CHILD_NAME, mask_child_names
from markup import make_markup

#: A pack's eye line the safety check stopped or flagged goes in the review queue once per
#: presence episode; outside an episode (no one has arrived yet, or they are gone), once
#: per this many seconds per pack, module, line and verdict (`_journal_eye_line`).
EYE_REPEAT_WINDOW_S = 600.0
#: How many such lines each robot remembers for that rule: a bound, not a policy (the
#: oldest is forgotten first).
EYE_REPEAT_MAX = 64


class PresenceMixin:
    # ---- presence: the robot's own eyes (vision.md) ----
    # Vision runs on-device; the robot sends only semantic strings (`eb-found-face`,
    # `eb-lost-target`, `eb-qr-event`, …) with no position or identity. They arrive as the
    # `speech` of an ordinary RemoteChatRequest, and a reply is REQUIRED (OpenMoxie
    # RemoteModuleAPI.md §Event Handling). Nothing arrives until the active module opts in
    # via `RemoteChatAction.EventSubscription` (`_vision_subscription`). Inferred from the
    # recovered catalog; no physical robot has sent us one yet.

    def _presence_state(self, robot) -> dict:
        """This robot's raw presence record (`moxie_sdk.presence.new_state()` shape)."""
        st = robot.extra.get("presence")
        return st if isinstance(st, dict) else presence_seam.new_state()

    def _ingest_vision(self, device_id, robot, name, payload, now=None) -> list:
        """Fold one vision event into the robot's presence state; return its signals.
        The state is rebuilt by the pure helper (the MQTT loop only swaps a reference),
        and the app's `on_event` hook sees every event."""
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
        """A vision event that arrived as a chat turn. Always answered, never with a
        brain call: a subscribed content pack's rule, else a greeting and/or a launch card,
        else `NOREPLY_ACK` (ResultCode 6, "heard you, saying nothing").

        The pack is offered the event first; if a rule answers, that is the whole reply
        (no greeting, no card, no `greeted_at` stamp). Launch cards are exercised only by
        the SIL robot and browser SIM so far (backlog/qr-launch-cards.md §7)."""
        event_id = rcr.get("event_id")
        backend = rcr.get("backend", "router")
        input_vars = rcr.get("input_vars") or {}
        signals = self._ingest_vision(device_id, robot, name, input_vars)
        # Inbound half of `subscribe`: offer the event to the pack's local evaluator.
        if self._wake_subscribed_pack(device_id, robot, rcr, name, input_vars):
            return None
        # A printed launch card: the only place a scanned QR value is in scope while a
        # reply is built. The decoder (`moxie_sdk/launch_cards.py`) is closed; anything it
        # refuses comes back None and is just a QR we ignore.
        card = cards_seam.decode_event(name, input_vars)
        greeting = self._greeting_for(device_id, robot, signals)
        if greeting is None and card is None:
            return self._publish_chat(device_id, event_id, backend, "", markup="",
                                      result=ResultCode.NOREPLY_ACK)
        # A card and a hello can share one reply (one greeting, one launch).
        text, markup = greeting if greeting is not None else ("", "")
        if greeting is not None:
            self._note("chat", f"hello (unprompted): '{self._masked(text, 40)}'")
            print(f"[runtime] 👋 {device_id} walked back in -> '{self._masked(text)}'",
                  flush=True)
        if card is not None:
            self._note("vision", f"🎴 launch card -> {card.module_id}")
            print(f"[runtime] 🎴 {device_id} scanned a launch card -> {card.module_id}",
                  flush=True)
        # A card alone launches silently: no invented line.
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
        """Offer a perceived event to the app that asked for it. True iff it answered.

        Perception must never cost a model call (vision.md §7.1 — `eb-found-face` fires
        whenever a child moves), so this calls `MoxieApp.perceive` (ContentApp: the
        sandboxed evaluator only), never `respond`. Gates, mirroring the outbound ones:
        the pack asked for this event under the robot's *current* module (recorded by
        `_merge_subscriptions`); MOXIE_VISION is on; the robot is permitted; the app
        implements `perceive`. A `perceive` that raises is logged and ignored.

        The answer's line passes the output check every line Moxie says passes (`_assess`,
        Moxie's side). The child did not prompt it, so a line the check stops is not swapped
        for a redirect: the event is answered `NOREPLY_ACK`, as when no presence line is
        due, and the line's actions, end_turn and subscribe go with it. A flagged line is
        said. Either way the parent's review queue gets the verdict once per presence
        episode (`_journal_eye_line`) with the child's name masked in its excerpt, and the
        feed and the log name the pack and module. A clean line goes out as it came.
        """
        # Separate `if`s so each gate can be mutated independently (subscribe_mutation_check.py).
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
        # History is read, never written: the event stays out of the transcript (§7.1).
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
        verdict = self._assess(text, safety_seam.MOXIE)
        if verdict:
            # The pack and the item the robot is in (the console lists it as module/content).
            source = "/".join(p for p in (getattr(app, "name", "") or type(app).__name__,
                                          module, getattr(robot, "content_id", "") or "")
                              if p)
            verdict.excerpt = self._masked(verdict.excerpt)   # the queue keeps no name
            self._journal_eye_line(device_id, robot, source, text, verdict)
            if verdict.action == safety_seam.BLOCK:
                # Words the child never asked for: nothing is said in their place, and no
                # act or wake rides on them.
                self._note("vision", f"🧬 a content pack ({source}) answered {name} with a "
                                     f"line the safety check stopped: Moxie said nothing")
                print(f"[runtime] 🧬 {device_id}: {name} woke a content pack "
                      f"({self._masked(source)}); the safety check stopped its line, so "
                      f"Moxie said nothing", flush=True)
                self._publish_chat(device_id, rcr.get("event_id"),
                                   rcr.get("backend", "router"), "", markup="",
                                   result=ResultCode.NOREPLY_ACK)
                return True
        markup, scored = (getattr(reply, "markup", None), None)
        if text:
            markup, scored = self._stage(text, reply, turn_key=rcr.get("event_id"),
                                         chunk_index=0, markup=reply.markup)
        if verdict:
            self._note("vision", f"🧬 a content pack ({source}) answered {name} with a line "
                                 f"the safety check flagged: '{self._masked(text, 40)}'")
            print(f"[runtime] 🧬 {device_id}: {name} woke a content pack "
                  f"({self._masked(source)}) -> '{self._masked(text, 60)}' (flagged)",
                  flush=True)
        else:
            self._note("vision", f"🧬 a pack answered {name}: '{self._masked(text, 40)}'")
            print(f"[runtime] 🧬 {device_id}: {name} woke a content pack -> "
                  f"'{self._masked(text, 60)}'", flush=True)
        self._publish_chat(device_id, rcr.get("event_id"),
                           rcr.get("backend", "router"), text, markup or "",
                           actions=actions or None,
                           end_turn=getattr(reply, "end_turn", False),
                           result=getattr(reply, "result_code", ResultCode.SUCCESS),
                           scored=scored, subscribe=subscribe or None)
        if text:
            self._maybe_synthesize(device_id, markup, rcr.get("event_id"), chunk_num=0)
        return True

    def _journal_eye_line(self, device_id, robot, source, text, verdict) -> None:
        """Put a pack's stopped or flagged eye line in the parent's review queue once per
        presence episode, per pack and module (`source`), line and verdict. A face at the
        frame edge sends an event per blink (vision.md §7.2) and the queue keeps only its
        newest rows, so a repeat is not a row: it raises the `count` of the row it repeats
        (a row with no `count` stands for one event).

        The episode is the presence run an `arrived` opened (`present_since`, `faces_seen`).
        It lasts through every flicker, a `left` the face came straight back from included
        (twenty blinks make one `arrived` and one `left`), and ends once the face has been
        gone `FLICKER_S`: the next found is a new arrival. Outside an episode (no one has
        arrived yet, or they are gone) a repeat counts on its row for
        `EYE_REPEAT_WINDOW_S` instead."""
        now = time.time()
        state = self._presence_state(robot)
        since, gone = state.get("present_since"), state.get("absent_since")
        in_run = since is not None and (
            state.get("face_present") is True
            or (gone is not None and now - gone < presence_seam.FLICKER_S))
        episode = (since, state.get("faces_seen")) if in_run else None
        key = (source, text, verdict.action, tuple(verdict.categories))
        with self._presence_lock:
            seen = self.__dict__.setdefault("_eye_journaled", {}).setdefault(device_id, {})
            last = seen.get(key)
            if last is None:
                repeat = False
            elif episode is not None:
                repeat = last["episode"] == episode
            else:
                repeat = (last["episode"] is None
                          and now - last["at"] < EYE_REPEAT_WINDOW_S)
            if repeat:
                last["count"] += 1
                row_id, count = last["row"], last["count"]
        if not repeat:
            row = self._record_safety(device_id, verdict)
            with self._presence_lock:
                seen.pop(key, None)
                seen[key] = {"episode": episode, "at": now, "count": 1,
                             "row": (row or {}).get("id")}
                while len(seen) > EYE_REPEAT_MAX:
                    del seen[next(iter(seen))]
            return
        if not row_id:
            return                                # counts only (NO_DATA): no row to raise
        events = safety_seam.EVENTS_COLLECTION
        try:
            # Read, raise and write as one transaction: a row appended meanwhile (a child's
            # turn) must not be lost to this write.
            with self.store.transaction(device_id, events):
                rows = self.store.read(device_id, events, [])
                for row in rows if isinstance(rows, list) else []:
                    if isinstance(row, dict) and row.get("id") == row_id:
                        row["count"] = count
                        self.store.write(device_id, events, rows)
                        break
        except Exception as e:
            print(f"[runtime] safety journal write failed: {e}", flush=True)

    def _greeting_for(self, device_id, robot, signals):
        """`(text, markup)` if this robot has earned an unprompted hello, else None.

        Gates: an `arrived` signal with `away_s >= greet_after_s` (0 = off; a first
        sighting never greets); once per absence (`greeted_at` vs `last_lost_at`); never
        over a turn in flight (queued for `_speak_opener`); never to an unpermitted robot;
        never in bedtime hours. The line itself passes the output safety check
        (`_speakable_hello`), queued or not.
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
        name = robot.child.nickname
        with self._presence_lock:
            state = self._presence_state(robot)
            greeted_at = state.get("greeted_at")
            lost_at = state.get("last_lost_at") or 0.0
            if greeted_at is not None and greeted_at >= lost_at:
                return None                       # already said hello for this absence
            last = self._last_greeting.get(device_id, "")
            state = dict(state)
            state["greeted_at"] = now             # one hello per absence, said or not
            robot.extra["presence"] = state
        # Outside the lock: a classifier may be slow, and a block writes the review queue.
        text = self._speakable_hello(device_id, presence_seam.pick_greeting(name, last),
                                     name, last)
        if not text:
            return None
        with self._presence_lock:
            self._last_greeting[device_id] = text
            busy = device_id in self._busy
            if busy:
                self._pending_opener[device_id] = text
        if busy:
            self._note("vision", f"hello queued (turn in flight) for {device_id}")
            print(f"[runtime] 👋 queued opener for {device_id} (turn in flight)", flush=True)
            return None
        return text, make_markup(text, turn_key=f"greet|{device_id}|{now:.0f}",
                                 chunk_index=0)

    def _speakable_hello(self, device_id, text, name, last) -> str:
        """`text`, a hello naming the child, as it may be said: through the output check
        every other line Moxie says passes (`_assess`, Moxie's side; the preview, telehealth
        and the brain's answers do the same).

        The name was checked when it was saved (`cloud_config.check_name`); this is the
        defence in depth for one that never was (the appliance's own
        `MOXIE_CHILD_NICKNAME`, a record an older build wrote, a classifier stricter than
        the table). A BLOCKED hello becomes the generic one (`GENERIC_CHILD_NAME`), and
        only the parent hears of it: the block in the safety review queue (its excerpt
        with the name masked) and one feed line. A flagged hello is said and recorded, as
        a flagged answer is. `""` when even the generic hello is blocked (a rules table
        can block anything): then there is no hello."""
        verdict = self._assess(text, safety_seam.MOXIE)
        if not verdict:
            return text
        verdict.excerpt = mask_child_names(text, [name])   # the queue never keeps a name
        self._record_safety(device_id, verdict)
        if verdict.action != safety_seam.BLOCK:
            return text
        generic = presence_seam.pick_greeting(GENERIC_CHILD_NAME, last)
        again = self._assess(generic, safety_seam.MOXIE)
        if again and again.action == safety_seam.BLOCK:
            self._note("safety", f"🛑 no hello for {device_id}: the safety rules block the "
                                 f"hello that names the child and the generic one too")
            return ""
        self._note("safety", f"🛑 the hello for {device_id} named the child, and the "
                              f"safety rules block that name "
                              f"({', '.join(verdict.categories)}): Moxie said the generic "
                              f"hello instead. Give the child another name in the Wi-Fi tab.")
        return generic

    def _speak_opener(self, device_id, event_id, seq):
        """Deliver a queued hello as chunk 0 of the starting turn — the filler wire shape
        (`REPLY_PENDING`, `chunk_num=0`), so the real answer follows as chunk 1. Returns the
        text, or None."""
        with self._presence_lock:
            text = self._pending_opener.pop(device_id, None)
        if not text or self._is_stale(device_id, seq):
            return None
        markup, scored = self._stage(text, turn_key=f"greet|{event_id}", chunk_index=0)
        self._note("chat", f"hello (queued): '{self._masked(text, 40)}'")
        print(f"[runtime] 👋 delivering queued opener on {device_id}: "
              f"'{self._masked(text)}'", flush=True)
        self._publish_chat(device_id, event_id, "router", text, markup,
                           result=ResultCode.REPLY_PENDING, chunk_num=0,
                           is_completed=False, scored=scored)
        self._maybe_synthesize(device_id, markup, event_id, chunk_num=0)
        return text

    def _in_bedtime(self, device_id, now=None) -> bool:
        """True inside the robot's bedtime window from the effective config
        (`weekday_bedtime`/`weekend_bedtime` "HH:MM", midnight wrap handled), judged on the
        house's clock (`house_now`), never the server's. No window -> never bedtime."""
        from moxie_sdk.cloud_config import in_bedtime
        try:
            cfg = self.effective_config(device_id)
        except Exception:
            return False
        return in_bedtime(cfg, self.house_now(device_id, now, cfg=cfg))

    # ---- the house's clock (cloud_config § the house's clock) ----
    def house_zone(self, device_id, cfg=None):
        """This robot's house clock, `HouseZone(tz, name, resolved)`: the `timezone_id` of
        its effective config (its own, else the house rule, else `MOXIE_TIMEZONE`), else
        `DEFAULT_TIMEZONE_ID`: the zone its config push names. Never raises. A zone this
        server cannot resolve is UTC, said once per robot and name in the feed, so a missing
        tz database or an old typo shows instead of quietly moving bedtime."""
        from moxie_sdk.cloud_config import DEFAULT_TIMEZONE_ID, known_timezones, resolve_zone
        if cfg is None:
            try:
                cfg = self.effective_config(device_id)
            except Exception:
                cfg = {}
        name = (cfg if isinstance(cfg, dict) else {}).get("timezone_id") or DEFAULT_TIMEZONE_ID
        zone = resolve_zone(name)
        if not zone.resolved:
            noted = self.__dict__.setdefault("_zone_noted", set())
            if (device_id, str(name)) not in noted:
                noted.add((device_id, str(name)))
                why = ("this server has no time zone database (the tzdata package)"
                       if not known_timezones() else "this server does not know that zone")
                self._note("error", f"🕰️ {device_id or 'house rule'}: time zone "
                                    f"{str(name)[:64]!r} cannot be read ({why}), so bedtime, "
                                    f"the day plan and the clock run on UTC until a parent "
                                    f"picks the zone in Settings")
        return zone

    def house_now(self, device_id, at=None, *, cfg=None):
        """`at` (epoch seconds; now when None) on this robot's house clock (`house_zone`):
        an aware `datetime`, so the house's wall clock and the epoch stay one instant."""
        import datetime
        return datetime.datetime.fromtimestamp(time.time() if at is None else float(at),
                                               self.house_zone(device_id, cfg).tz)

    def _vision_subscription(self, device_id, robot=None):
        """The `EventSubscription.active[]` list to attach to this response, or None.
        Re-sent once per `(device, module_id)`: subscriptions end when a module exits
        (RemoteModuleAPI §Unsubscribing). MOXIE_VISION=0 turns it off."""
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
        """The runtime's own subscription list (`mine`) plus what this reply asked for
        (`asked`, e.g. a content pack's `subscribe`). Returns the merged list or None.

        Direction matters: `mine` always survives in full. `_vision_subscription` has
        already latched as sent, so a merge that let `asked` replace it would silently
        switch presence off for that module. `asked` is gated by MOXIE_VISION and the
        pairing gate, and bounded to `presence_seam.VISION_EVENTS` (the only events we can
        route). Side effect: each accepted request is recorded in `_pack_subscribed`,
        the only record `_wake_subscribed_pack` consults on the way back in.
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
                # The module this request is made under (a request under A must not wake B).
                module = (getattr(self.robots.get(device_id), "module_id", None) or "")
                for event in asked:
                    name = str(event)
                    if name not in presence_seam.VISION_EVENTS:
                        print(f"[runtime] 👁️  {name!r} is not an event this appliance "
                              f"can route; dropped", flush=True)
                        continue
                    # The record the inbound half reads. Recorded even when the event is
                    # already in `merged`, or a pack asking for a runtime-owned event could
                    # never be woken by it.
                    with self._presence_lock:
                        self._pack_subscribed.setdefault(device_id, {})[name] = module
                    if name not in merged:
                        merged.append(name)
                        self._note("vision", f"a content pack asked to be told about "
                                             f"{name} on {device_id}")
        return merged or None
