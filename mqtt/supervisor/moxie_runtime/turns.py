"""Conversation turns: routing, latency budget + fillers, streaming, and the chat reply wire."""
from __future__ import annotations
import json, threading

from moxie_sdk.types import Turn, Reply, ReplyChunk, RobotContext, ResultCode
from moxie_sdk.wire import (build_chat_response, build_activity_response,
                            build_remote_modules, is_module_query)
from moxie_sdk.filler import pick_filler
from moxie_sdk import safety as safety_seam
from moxie_sdk import presence as presence_seam
from moxie_sdk import telehealth as telehealth_seam
from moxie_sdk import vocab as vocab_seam
from markup import perform
from .constants import MAX_FILLERS_PER_TURN


class TurnsMixin:
    def _turn_worker(self, device_id, event_id, speech, turn, seq):
        """`_handle_turn` with an in-flight marker (read by `_greeting_for` to queue a hello)."""
        with self._presence_lock:
            self._busy.add(device_id)
        try:
            self._handle_turn(device_id, event_id, speech, turn, seq)
        finally:
            with self._presence_lock:
                self._busy.discard(device_id)

    # ---- events ----
    def _on_event(self, device_id, name, payload):
        # An event from an unknown robot registers it, as `/state` does: after a supervisor
        # restart the robot may never re-announce itself. The pairing gate already ran in
        # `_on_message`.
        self._device_connect(device_id)
        robot = self.robots.get(device_id) or RobotContext(device_id=device_id, child=self.child)
        if name.startswith("remote-chat"):
            return self._on_remote_chat(device_id, robot, payload)
        if name == "zmq":
            return self.handle_zmq(device_id, payload)
        if name == "client-service-activity-log":
            return self._on_activity(device_id, payload)
        if name in ("telemetry", "analytics") or name.startswith("packet"):
            return self.ingest_telemetry(device_id, payload)
        try:
            data = json.loads(payload)
        except Exception:
            data = {"raw": True}
        # A vision event on its own subtopic (defensive: the contract delivers them inside
        # a RemoteChatRequest). No request to answer, so a hello is queued for the next turn.
        if presence_seam.is_vision_event(name):
            self.robots.setdefault(device_id, robot)
            signals = self._ingest_vision(device_id, robot, name,
                                          data.get("input_vars") or data)
            greeting = self._greeting_for(device_id, robot, signals)
            if greeting is not None:
                with self._presence_lock:
                    self._pending_opener[device_id] = greeting[0]
                self._note("vision", f"hello queued (no request to answer) for {device_id}")
            return None
        # everything else → surface to the app as an event (module lifecycle…)
        try:
            self.app_for(device_id).on_event(robot, name, data)
        except Exception:
            pass

    def _on_remote_chat(self, device_id, robot, payload):
        try:
            rcr = json.loads(payload)
        except Exception:
            return
        command = rcr.get("command", "prompt")
        backend = rcr.get("backend", "router")
        event_id = rcr.get("event_id")
        # A module switch ends the previous conversation (see `_end_conversation`).
        new_module = rcr.get("module_id")
        if new_module and robot.module_id and new_module != robot.module_id:
            self._end_conversation(device_id, "module_switch", robot=robot)
        robot.module_id = rcr.get("module_id") or robot.module_id
        robot.content_id = rcr.get("content_id") or robot.content_id

        # module list query (backend:data, RemoteDataQuery{query: modules}) → the
        # remote-chat modules this appliance serves, before any brain is consulted
        if is_module_query(rcr):
            return self._publish_chat(device_id, event_id, backend, "", markup="",
                                      result=ResultCode.SUCCESS,
                                      query_data=build_remote_modules(self.remote_modules()))

        # rebuild history from notify events (Moxie is authoritative about what it said)
        if command == "notify":
            return self._ingest_notify(device_id, rcr)

        # No brain while a telehealth session is open: two voices in one mouth is what
        # puppet mode exists to avoid (backlog/telehealth.md B3).
        if self._telehealth.get(device_id, {}).get("session_id"):
            self._note("telehealth", "ignored a remote-chat during a session")
            return None

        speech = rcr.get("speech") or ""
        for ln in rcr.get("extra_lines", []) or []:
            if ln.get("context_type") == "input" and ln.get("text"):
                speech = ln["text"]
        # A subscribed perception event arrives in the `speech` slot (RemoteModuleAPI
        # §Event Handling): answered here, never sent to a brain, history or safety.
        if presence_seam.is_vision_event(speech):
            return self._on_vision_turn(device_id, robot, rcr, speech.strip())
        turn = Turn(robot=robot, speech=speech, history=list(self.history.get(device_id, [])),
                    command=command, input_vars=rcr.get("input_vars", {}),
                    presence=presence_seam.snapshot(self._presence_state(robot)))
        # Number the turn so a slow answer can be recognized as stale (`_is_stale`). The
        # MQTT loop is the only writer, so a plain increment is enough.
        seq = self._turn_seq[device_id] = self._turn_seq.get(device_id, 0) + 1
        # Run the (possibly slow) app + LLM off the MQTT loop so we never block it.
        self._pool.submit(self._turn_worker, device_id, event_id, speech, turn, seq)

    # ---- one turn, with a latency budget ----
    def _is_stale(self, device_id, seq) -> bool:
        """True when a newer turn for this robot started after `seq` (never speak it)."""
        return seq is not None and self._turn_seq.get(device_id, seq) != seq

    def _handle_turn(self, device_id, event_id, speech, turn, seq=None):
        """Answer one turn. Fast brain -> exactly one SUCCESS reply.

        Slow brain (over `brain_budget_s`): a filler now as chunk 0 with `REPLY_PENDING`
        (RemoteChat.proto ResultCode 9), then the real line as chunk 1 with SUCCESS +
        `is_completed` — without it a slow brain overruns the robot's ~20 s re-prompt.
        Streaming brain (MOXIE_STREAMING, default on): see `_handle_stream_turn`.
        Safety (ai-seam §2) wraps both ends: input before the brain, output before publish.
        The interlude idea is OpenMoxie Fork A's `ReasoningChatSession`; the code is ours.
        """
        if self._safety_gate_input(device_id, event_id, speech, seq):
            return
        # Resolve the brain once; a mid-turn swap applies from the next turn.
        app = self.app_for(device_id)
        if self.streaming:
            stream = None
            try:
                stream = app.respond_stream(turn)
            except Exception as e:
                print(f"[runtime] app.respond_stream error: {e}", flush=True)
            if stream is not None:
                return self._handle_stream_turn(device_id, event_id, speech, turn,
                                                seq, stream, app=app)
        state = {"lock": threading.Lock(), "done": False, "filler": None}
        # A hello queued during the previous turn rides out as chunk 0.
        state["filler"] = self._speak_opener(device_id, event_id, seq)
        timer = None
        if self.brain_budget_s > 0:
            timer = threading.Timer(self.brain_budget_s, self._speak_filler,
                                    args=(device_id, event_id, seq, state))
            timer.daemon = True
            timer.start()
        try:
            reply = self._safe_respond(turn, app=app)
        finally:
            if timer is not None:
                timer.cancel()
        # Close the filler door atomically: the timer holds this lock while publishing.
        with state["lock"]:
            state["done"] = True
            filler = state["filler"]
        if self._is_stale(device_id, seq):
            self._note_stale(device_id, seq, f"dropping '{reply.text[:40]}'")
            return
        # A blocked answer is never published: a safe line goes out and is journaled.
        out_verdict = self._assess(reply.text, safety_seam.MOXIE)
        if out_verdict:
            if out_verdict.action == safety_seam.BLOCK:
                red = self._safety_redirect(device_id, out_verdict)
                reply = Reply(text=red.text, markup=red.markup,
                              result_code=reply.result_code)
            else:
                self._record_safety(device_id, out_verdict)
        self._remember(device_id, speech, reply.text)
        markup, scored = self._stage(reply.text, reply, turn_key=event_id,
                                     chunk_index=0, markup=reply.markup)
        self._log_exchange(device_id, speech, reply.text)
        # After a filler this is chunk 1 and closes the sequence; else a single reply.
        chunk = 1 if filler is not None else None
        # `scored` carries the app's mood/dialog_act already validated by `_stage`; don't
        # pass raw `reply.mood` too (it would bypass the catalog check).
        self._publish_chat(device_id, event_id, "router", reply.text, markup,
                           actions=reply.actions, end_turn=reply.end_turn,
                           result=reply.result_code, chunk_num=chunk,
                           is_completed=None if chunk is None else True,
                           scored=scored,
                           # A content pack's `Reply.subscribe`, merged in `_publish_chat`.
                           subscribe=reply.subscribe)
        self._maybe_synthesize(device_id, markup, event_id, chunk_num=chunk or 0)
        # `<exit>` ended the activity; we are off the MQTT loop, so summarize inline.
        self._maybe_end_conversation(device_id, reply.actions)

    def _speak_filler(self, device_id, event_id, seq, state):
        """The budget expired with the brain still thinking: speak a filler now as chunk 0
        / REPLY_PENDING (synthesized, never the same line twice running). Returns the text,
        or None if the brain won or the turn is stale."""
        with state["lock"]:
            if state["done"]:
                return None                       # brain won the race — say nothing
            if self._is_stale(device_id, seq):
                return None
            state["filler"] = self._say_filler(
                device_id, event_id, 0, f"brain over budget ({self.brain_budget_s:g}s)")
            return state["filler"]

    # ---- one turn, streamed sentence by sentence ----
    def _handle_stream_turn(self, device_id, event_id, speech, turn, seq, stream,
                            app=None):
        """Answer a turn from an `Iterator[ReplyChunk]`, publishing as the model writes.

        Each sentence goes out as `REPLY_PENDING` with its `chunk_num`; the `final` chunk
        closes with SUCCESS + `is_completed` (remote-chat-protocol.md). A one-chunk answer
        is published exactly like a non-streamed one. The filler timer re-arms after every
        chunk (at most `MAX_FILLERS_PER_TURN`); fillers take the next `chunk_num`. A newer
        turn cancels the stream and nothing more is published for the old `event_id`.
        """
        state = {"lock": threading.Lock(), "done": False, "chunk": 0, "ans": 0,
                 "fillers": 0, "gen": 0, "timer": None}
        said, closed, failed = [], False, None
        acts: list = []                 # every action the stream asked for (e.g. <exit>)
        # A queued hello takes chunk 0; `ans` stays 0 so the answer keeps its mood.
        opener = self._speak_opener(device_id, event_id, seq)
        if opener:
            state["chunk"] = 1
            said.append(opener)
        self._arm_filler(device_id, event_id, seq, state)
        try:
            for chunk in stream:
                final = bool(getattr(chunk, "final", False))
                # Assessed per chunk before publishing (the rest does not exist yet). A
                # blocked chunk is never spoken: close on a safe line, cancel the stream.
                verdict = self._assess(chunk.text, safety_seam.MOXIE)
                blocked = bool(verdict) and verdict.action == safety_seam.BLOCK
                red = None
                if blocked:
                    red = self._safety_redirect(device_id, verdict)
                elif verdict:
                    self._record_safety(device_id, verdict)
                with state["lock"]:
                    state["gen"] += 1                 # invalidate any in-flight timer
                    self._cancel_filler(state)
                    stale = self._is_stale(device_id, seq)
                    if not stale:
                        if final or blocked:
                            state["done"] = True
                        n = state["chunk"]
                        state["chunk"] = n + 1
                        a = state["ans"]
                        state["ans"] = a + 1
                        if blocked:
                            safe = ReplyChunk(text=red.text, markup=red.markup, final=True)
                            self._publish_stream_chunk(device_id, event_id, safe, n, True,
                                                       ann=a)
                            said.append(red.text)
                        else:
                            self._publish_stream_chunk(device_id, event_id, chunk, n,
                                                       final, ann=a)
                            if chunk.text:
                                said.append(chunk.text)
                            acts += list(getattr(chunk, "actions", None) or [])
                if stale:
                    self._note_stale(device_id, seq, "cancelling the stream mid-answer",
                                     what="cancelled a stale stream")
                    return
                if final or blocked:
                    closed = True
                    break
                self._arm_filler(device_id, event_id, seq, state)
        except Exception as e:
            failed = e
            print(f"[runtime] app.respond_stream error: {e}", flush=True)
        finally:
            with state["lock"]:
                state["done"] = True
                self._cancel_filler(state)
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        if self._is_stale(device_id, seq):
            self._note("chat", f"⏭️  dropped a stale answer for {device_id}")
            return
        if not closed:
            # No final chunk: nothing spoken yet -> answer on the non-streaming path;
            # otherwise close the sequence so the robot is not left waiting.
            with state["lock"]:
                n = state["chunk"]
            if n == 0:
                reply = (self._safe_respond(turn, app=app) if failed is not None
                         else Reply(text=""))
                said.append(reply.text)
                self._publish_stream_chunk(device_id, event_id, reply, 0, True, ann=0)
            else:
                self._publish_stream_chunk(
                    device_id, event_id, Reply(text=""), n, True, synthesize=False)
        text = " ".join(t for t in said if t).strip()
        self._remember(device_id, speech, text)
        self._log_exchange(device_id, speech, text, f" ({state['chunk']} chunk(s))")
        self._maybe_end_conversation(device_id, acts)

    def _say_filler(self, device_id, event_id, n, why) -> str:
        """Publish (and voice) one filler as chunk `n` / REPLY_PENDING — never the same
        line twice running. The caller holds the turn's state lock."""
        text, markup = pick_filler(self._last_filler.get(device_id, ""))
        self._last_filler[device_id] = text
        self._note("chat", f"⏳ '{text[:40]}'")
        print(f"[runtime] ⏳ {why} on {device_id} → filler: '{text}'", flush=True)
        _, scored = self._stage(text, turn_key=event_id, chunk_index=n, markup=markup)
        self._publish_chat(device_id, event_id, "router", text, markup,
                           result=ResultCode.REPLY_PENDING, chunk_num=n,
                           is_completed=False, scored=scored)
        self._maybe_synthesize(device_id, markup, event_id, chunk_num=n)
        return text

    def _note_stale(self, device_id, seq, detail, what="dropped a stale answer"):
        self._note("chat", f"⏭️  {what} for {device_id}")
        print(f"[runtime] ⏭️  turn {seq} superseded on {device_id}; {detail}", flush=True)

    def _log_exchange(self, device_id, speech, text, suffix=""):
        self._note("chat", f"💬 '{speech[:30]}' → '{text[:40]}'")
        print(f"[runtime] 💬 {device_id}: '{speech[:40]}' → '{text[:60]}'{suffix}",
              flush=True)

    def _safe_respond(self, turn, app=None):
        """One non-streamed answer from `app` (the appliance's own when None)."""
        try:
            return (app if app is not None else self.app).respond(turn)
        except Exception as e:
            print(f"[runtime] app.respond error: {e}", flush=True)
            return Reply(text="Hmm, let me think about that.")

    def _stage(self, text, obj=None, *, turn_key="", chunk_index=0, markup=None, **kw):
        """`(markup, scored)` for one spoken line: the single place a published line
        becomes a scored turn (expressiveness.md §2.3).

        The app's own scoring wins field by field and is also fed to the planner as hints,
        so the performance agrees with the wire fields; the planner fills what the app left
        None. Authored `markup` is spoken verbatim and still scored.
        """
        hints = dict(kw)
        for attr, key in (("mood", "mood_hint"), ("gesture", "gesture_hint"),
                          ("dialog_act", "dialog_act"), ("emotion", "emotion"),
                          ("signal", "signal"), ("gaze", "look"), ("icon", "icon"),
                          ("sfx", "sfx")):
            value = getattr(obj, attr, None)
            if value:
                hints.setdefault(key, value)
        if getattr(obj, "mood_intensity", 0):
            hints.setdefault("intensity", obj.mood_intensity)
        staged = perform(text, turn_key=turn_key, chunk_index=chunk_index, **hints)
        scored = dict(staged.scored)
        # App values win but pass the same catalog as every other id: an invented
        # `dialog_act` is dropped rather than forwarded (mutation M28).
        for key, catalog in (("mood", vocab_seam.MOODS),
                             ("dialog_act", vocab_seam.DIALOG_ACTS),
                             ("emotion", vocab_seam.EMOTION_STATES),
                             ("signal", vocab_seam.SIGNALS)):
            value = getattr(obj, key, None)
            if value and value in catalog:
                scored[key] = value
        strength = getattr(obj, "mood_intensity", 0)
        if strength and 0 < int(strength) <= vocab_seam.MAX_INTENSITY:
            scored["mood_intensity"] = int(strength)
        if obj is not None and getattr(obj, "performance", None) is None:
            try:                      # diagnostics + the preview panel; never the wire
                object.__setattr__(obj, "performance", staged.performance)
            except Exception:
                pass
        return (staged.markup if markup is None else markup), scored

    def _publish_stream_chunk(self, device_id, event_id, chunk, n, final,
                              synthesize=True, ann=None):
        """One `ReplyChunk` (or `Reply`) onto the wire. `ann` is the index within the
        answer (fillers excluded): the mood rides index 0 only, so a streamed answer holds
        one face and a leading filler does not steal it."""
        markup, scored = self._stage(chunk.text, chunk, turn_key=event_id,
                                     chunk_index=n if ann is None else ann,
                                     markup=chunk.markup)
        result = getattr(chunk, "result_code", None)
        if result is None:
            result = ResultCode.SUCCESS if final else ResultCode.REPLY_PENDING
        # A one-chunk answer omits chunk_num/is_completed (the proto defaults).
        solo = final and n == 0
        self._publish_chat(device_id, event_id, "router", chunk.text, markup,
                           actions=chunk.actions, end_turn=chunk.end_turn,
                           result=result,
                           chunk_num=None if solo else n,
                           is_completed=None if solo else bool(final),
                           scored=scored)
        if synthesize:
            self._maybe_synthesize(device_id, markup, event_id, chunk_num=n)

    # ---- filler timer (shared by the streaming path) ----
    def _cancel_filler(self, state):
        timer = state.get("timer")
        state["timer"] = None
        if timer is not None:
            timer.cancel()

    def _arm_filler(self, device_id, event_id, seq, state):
        """(Re)start a streaming turn's latency timer; no-op when done or out of fillers."""
        if self.brain_budget_s <= 0:
            return
        with state["lock"]:
            if state["done"] or state["fillers"] >= MAX_FILLERS_PER_TURN:
                return
            timer = threading.Timer(self.brain_budget_s, self._speak_stream_filler,
                                    args=(device_id, event_id, seq, state, state["gen"]))
            timer.daemon = True
            state["timer"] = timer
        timer.start()

    def _speak_stream_filler(self, device_id, event_id, seq, state, gen):
        """The stream was silent for a whole budget: speak a filler (late first token, or
        once more for a mid-answer stall). `gen` is the chunk counter the timer was armed
        against; a chunk since then makes it a no-op."""
        with state["lock"]:
            if state["done"] or state["gen"] != gen:
                return None                       # a chunk arrived — nothing to cover
            if state["fillers"] >= MAX_FILLERS_PER_TURN:
                return None
            if self._is_stale(device_id, seq):
                return None
            state["fillers"] += 1
            state["gen"] += 1
            state["timer"] = None
            n = state["chunk"]
            state["chunk"] = n + 1
            text = self._say_filler(
                device_id, event_id, n,
                f"stream quiet for {self.brain_budget_s:g}s (filler {state['fillers']})")
        self._arm_filler(device_id, event_id, seq, state)   # another stall? one more line
        return text

    def remote_modules(self) -> list:
        """`[(module_id, [content_id, …]), …]` this appliance answers over remote chat —
        the `RemoteDataBlock.modules` a module query gets (`wire.build_remote_modules`).

        The conversations of every loaded content module, plus the day plan's default
        chat (`FREE_CHAT/default`, schedule/catalog.py:131,:135): the schedule already
        hands that module to the cloud, and the robot can only run it once it has been
        told the module is `REMOTE_CHAT`. An LLM-only appliance therefore lists just the
        default chat. Built from what is loaded, never from a brain call.
        """
        from moxie_sdk.schedule.catalog import DEFAULT_TEMPLATE
        out: dict = {}
        for app in self._content_apps():
            module = getattr(app, "module", None)
            for conv in getattr(module, "conversations", None) or []:
                mid = getattr(conv, "module_id", "") or ""
                cid = getattr(conv, "content_id", "") or ""
                if mid and cid and cid not in out.setdefault(mid, []):
                    out[mid].append(cid)
        chat = DEFAULT_TEMPLATE["chat_request"]
        if chat["content_id"] not in out.setdefault(chat["module_id"], []):
            out[chat["module_id"]].append(chat["content_id"])
        return [(mid, list(cids)) for mid, cids in out.items()]

    def _query_payload(self, device_id, query):
        """The value for a CloudQuery — None means "send this field's empty value"."""
        if query == "schedule":
            try:
                return self.build_schedule_for(device_id)
            except Exception as e:
                print(f"[runtime] schedule build failed: {e}", flush=True)
                return None
        if query == "mentor_behaviors":
            return self.mentor_behaviors(device_id)
        return None                       # license: no license blobs to share (yet)

    def _on_activity(self, device_id, payload):
        try:
            data = json.loads(payload)
        except Exception:
            return
        query = data.get("query")
        subtopic = data.get("subtopic")
        # Pull queries ride subtopic='query' (mqtt-and-conversation.md); a bare `query` counts.
        if subtopic in (None, "", "query") and query in ("schedule", "mentor_behaviors",
                                                         "license"):
            # CloudQueryResponse: echo `request_id`, key the payload by its proto field.
            resp = build_activity_response(query, self._query_payload(device_id, query),
                                           request_id=data.get("request_id"))
            self._publish(f"/devices/{device_id}/commands/query_result", resp,
                          device_id=device_id, what="query_result")
            return resp
        # The robot's telehealth state report (READY / IN_SESSION / EXITING): stored.
        if subtopic == telehealth_seam.EVENT_SUBTOPIC:
            return self.ingest_telehealth_event(device_id, data)
        # Reports on the same topic: a finished/quit activity (drives FTUE and variety).
        if isinstance(data.get("mentor_behavior"), dict):
            return self.ingest_mentor_behavior(device_id, data)

    # ---- publish a chat response ----
    def _publish_chat(self, device_id, event_id, backend, text, markup="",
                      actions=None, end_turn=False, result=ResultCode.SUCCESS,
                      query_data=None, mood=None, dialog_act=None,
                      chunk_num=None, is_completed=None, safety=None, scored=None,
                      subscribe=None):
        # The runtime's vision subscription rides the first plain, action-free closing
        # reply per module — the only cloud->robot message that can carry
        # `EventSubscription` — so replies carrying a launch/exit keep their shape.
        mine = None
        if (self.vision and query_data is None and backend == "router" and not actions
                and result == ResultCode.SUCCESS and chunk_num in (None, 0)
                and self._vision_subscribed.get(device_id) !=
                    (getattr(self.robots.get(device_id), "module_id", None) or "")):
            mine = self._vision_subscription(device_id)
        # The app's `Reply.subscribe` is merged INTO the runtime's list, never over it, and
        # may ride any reply (a pack's opening `act` + `subscribe` must work together).
        subscribe = self._merge_subscriptions(device_id, mine, subscribe)
        # Explicit mood/dialog_act arguments beat the staged `scored` values.
        sc = dict(scored or {})
        resp = build_chat_response(event_id, text, markup, backend=backend,
                                   result=result, actions=actions, end_turn=end_turn,
                                   mood=mood or sc.get("mood"),
                                   dialog_act=dialog_act or sc.get("dialog_act"),
                                   query_data=query_data,
                                   chunk_num=chunk_num, is_completed=is_completed,
                                   safety=safety, subscribe_events=subscribe,
                                   mood_intensity=sc.get("mood_intensity"),
                                   emotion=sc.get("emotion"),
                                   signals=sc.get("signal"))
        self._publish(f"/devices/{device_id}/commands/remote_chat", resp,
                      device_id=device_id, what="remote_chat")
