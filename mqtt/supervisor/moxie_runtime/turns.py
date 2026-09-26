"""Conversation turns: routing, latency budget + fillers, streaming, and the chat reply wire."""
from __future__ import annotations
import json, threading

from moxie_sdk.types import Turn, Reply, ReplyChunk, RobotContext, ResultCode
from moxie_sdk.wire import build_chat_response, build_activity_response
from moxie_sdk.filler import pick_filler
from moxie_sdk import safety as safety_seam
from moxie_sdk import presence as presence_seam
from moxie_sdk import telehealth as telehealth_seam
from moxie_sdk import vocab as vocab_seam
from markup import perform
from .constants import MAX_FILLERS_PER_TURN


class TurnsMixin:
    def _turn_worker(self, device_id, event_id, speech, turn, seq):
        """`_handle_turn` with an in-flight marker around it.

        The marker is what `_greeting_for` reads to decide "queue the hello" instead of
        "say it now" — nothing else about the turn loop changes."""
        with self._presence_lock:
            self._busy.add(device_id)
        try:
            self._handle_turn(device_id, event_id, speech, turn, seq)
        finally:
            with self._presence_lock:
                self._busy.discard(device_id)

    # ---- events ----
    def _on_event(self, device_id, name, payload):
        # An event from a robot we do not know about **registers** it, exactly as
        # `_on_state` has always done ("fallback if we missed the log line"). The two
        # ingress paths were asymmetric and it mattered: `$SYS/broker/log` is published
        # live and never replayed (A15), and a real Moxie publishes `/state` on *its*
        # connect — so after a supervisor restart, with the robot still happily connected,
        # there is nothing to re-read and nothing to wait for. This path used to build an
        # **ephemeral** RobotContext and answer the turn from it forever: no config push,
        # no `app.on_connect`, no presence state, invisible in `/status`. Three lines, and
        # the difference between "the appliance recovered" and "the appliance is answering
        # a robot it does not know it has". The pairing gate is unaffected — it lives on
        # the transport boundary in `_on_message` and has already run by here.
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
        # A vision event on its own `events/<name>` subtopic. The recovered contract
        # delivers these inside a RemoteChatRequest instead (see the presence region), so
        # this branch is a defensive extra rather than an observed shape: it updates
        # presence and, because there is no request to answer, any hello it earns is
        # QUEUED for the next turn rather than published unsolicited.
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
        # A module switch ends the previous conversation — the `complete_handler`
        # moment for whatever was running before (see `_end_conversation`).
        new_module = rcr.get("module_id")
        if new_module and robot.module_id and new_module != robot.module_id:
            self._end_conversation(device_id, "module_switch", robot=robot)
        robot.module_id = rcr.get("module_id") or robot.module_id
        robot.content_id = rcr.get("content_id") or robot.content_id

        # module list query (backend:data / query:modules) → empty list for v1
        if backend == "data" and rcr.get("query") == "modules":
            return self._publish_chat(device_id, event_id, backend, "", markup="",
                                      result=ResultCode.SUCCESS, modules=[])

        # rebuild history from notify events (Moxie is authoritative about what it said)
        if command == "notify":
            return self._ingest_notify(device_id, rcr)

        # 🎭 No brain while a telehealth session is open. Whether a brain-less robot in
        # STATE_TELEBRAIN still emits `events/remote-chat` at all is unknown
        # (`backlog/telehealth.md` B3) — this `if` makes the design correct either way. A
        # brain reply racing the operator's line is the one failure a child would see as
        # broken, and two voices in one mouth is exactly what puppet mode exists to avoid.
        if self._telehealth.get(device_id, {}).get("session_id"):
            self._note("telehealth", "ignored a remote-chat during a session")
            return None

        speech = rcr.get("speech") or ""
        for ln in rcr.get("extra_lines", []) or []:
            if ln.get("context_type") == "input" and ln.get("text"):
                speech = ln["text"]
        # The robot's own eyes: a subscribed perception event arrives in the `speech`
        # slot, not as words a child said (RemoteModuleAPI §Event Handling). It is
        # answered here — never handed to a brain, never written to history, never
        # assessed as a child's utterance.
        if presence_seam.is_vision_event(speech):
            return self._on_vision_turn(device_id, robot, rcr, speech.strip())
        turn = Turn(robot=robot, speech=speech, history=list(self.history.get(device_id, [])),
                    command=command, input_vars=rcr.get("input_vars", {}),
                    presence=presence_seam.snapshot(self._presence_state(robot)))
        # Number the turn so a slow brain's answer can be recognized as stale if the
        # child has moved on by the time it lands (_is_stale). The MQTT loop is the only
        # writer here, so a plain increment is enough.
        seq = self._turn_seq[device_id] = self._turn_seq.get(device_id, 0) + 1
        # Run the (possibly slow) app + LLM off the MQTT loop so we never block it.
        self._pool.submit(self._turn_worker, device_id, event_id, speech, turn, seq)

    # ---- one turn, with a latency budget ----
    def _is_stale(self, device_id, seq) -> bool:
        """True when a newer turn for this robot started after `seq` — its answer must
        never be spoken: the child asked something else in the meantime."""
        return seq is not None and self._turn_seq.get(device_id, seq) != seq

    def _handle_turn(self, device_id, event_id, speech, turn, seq=None):
        """Answer one turn. Fast brain → exactly one SUCCESS reply, as always.

        **Slow brain (over `brain_budget_s`)** → the child hears a short filler *now*
        instead of silence: chunk 0 with `result=REPLY_PENDING` ("more chunks to come",
        RemoteChat.proto ResultCode 9 — remote-chat-protocol.md:63), the inference keeps
        running on this worker, and the real line follows as chunk 1 with
        `result=SUCCESS` + `consistency_control.is_completed` to close the sequence
        (RemoteChat.proto fields 22/18). Without this a 45 s brain overruns the robot's
        ~20 s reprompt window (openmoxie-feature-audit.md:347) and Moxie just goes quiet.

        **Streaming brain** (`MOXIE_STREAMING`, default on) → if the app offers a
        `respond_stream`, each finished sentence goes out as its own chunk the moment the
        model writes it, so the child hears real words at first-token latency instead of
        at whole-completion latency. See `_handle_stream_turn`.

        **Safety (ai-seam §2)** wraps both ends and is app-agnostic, because it lives here
        rather than in any `MoxieApp`: the child's utterance is assessed BEFORE the brain
        is called (`_safety_gate_input` — a hard block never reaches a model), and the
        brain's answer is assessed before it is published (per chunk when streaming).

        Pattern credit: OpenMoxie Fork A's `ReasoningChatSession` runs the long inference
        on a pool and speaks rotating interludes meanwhile; the idea is theirs, this code
        and the multi-chunk wire shape are ours.
        """
        if self._safety_gate_input(device_id, event_id, speech, seq):
            return
        # 🧠 Which brain answers THIS child (`app_for`) — resolved exactly once, here, and
        # carried through the turn. A parent who swaps brains while Moxie is mid-sentence
        # gets the new one on the next turn; this one finishes with what it started with.
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
        # A hello queued while a previous turn was in flight (someone walked in mid-answer)
        # rides out as this turn's chunk 0 — the filler's own wire shape, so the answer
        # below closes the sequence as chunk 1. Nothing else about the turn changes.
        state["filler"] = self._speak_opener(device_id, event_id, seq)
        timer = None
        if self.brain_budget_s > 0:
            timer = threading.Timer(self.brain_budget_s, self._speak_filler,
                                    args=(device_id, event_id, seq, state))
            timer.daemon = True
            timer.start()
        try:
            reply = app.respond(turn)
        except Exception as e:
            print(f"[runtime] app.respond error: {e}", flush=True)
            reply = Reply(text="Hmm, let me think about that.")
        finally:
            if timer is not None:
                timer.cancel()
        # Closing the door on the filler and reading it back is one atomic step: the
        # timer holds this same lock while it publishes, so chunk 0 can never land after
        # chunk 1.
        with state["lock"]:
            state["done"] = True
            filler = state["filler"]
        if self._is_stale(device_id, seq):
            self._note("chat", f"⏭️  dropped a stale answer for {device_id}")
            print(f"[runtime] ⏭️  turn {seq} superseded on {device_id}; "
                  f"dropping '{reply.text[:40]}'", flush=True)
            return
        # Post-inference: a non-streamed answer is assessed whole. A blocked answer is
        # never published — the child hears a safe line instead and it goes in the queue.
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
        self._note("chat", f"💬 '{speech[:30]}' → '{reply.text[:40]}'")
        print(f"[runtime] 💬 {device_id}: '{speech[:40]}' → '{reply.text[:60]}'", flush=True)
        # A filler already went out → this is chunk 1 and it ends the sequence. No
        # filler → the single-chunk reply we have always sent, unchanged on the wire.
        chunk = 1 if filler is not None else None
        # `scored` already carries the app's own mood/dialog_act — validated, because
        # `_stage` puts them through the same catalog every other id goes through. Passing
        # the raw `reply.mood`/`reply.dialog_act` here as well would let an app's invented
        # act win over that check inside `_publish_chat` (mutation M28's other half).
        self._publish_chat(device_id, event_id, "router", reply.text, markup,
                           actions=reply.actions, end_turn=reply.end_turn,
                           result=reply.result_code, chunk_num=chunk,
                           is_completed=None if chunk is None else True,
                           scored=scored,
                           # What this reply asked to PERCEIVE (`Reply.subscribe`, filled
                           # by a content pack's `subscribe` statement). Merged into the
                           # runtime's own vision subscription inside `_publish_chat`,
                           # which is the only place that merge is allowed to happen.
                           subscribe=reply.subscribe)
        self._maybe_synthesize(device_id, markup, event_id, chunk_num=chunk or 0)
        # `<exit>` in the model's own line (or a handler's) ended the activity: this
        # worker is already off the MQTT loop, so summarize inline.
        self._maybe_end_conversation(device_id, reply.actions)

    def _speak_filler(self, device_id, event_id, seq, state):
        """The budget expired with the brain still thinking → say something kind now.

        Published as chunk 0 / REPLY_PENDING, and synthesized like any other line so the
        SIM (and a robot without on-device TTS) actually hears it. Never the same line
        twice in a row for one robot. Returns the filler text, or None if the brain beat
        the budget or the turn is already stale."""
        with state["lock"]:
            if state["done"]:
                return None                       # brain won the race — say nothing
            if self._is_stale(device_id, seq):
                return None
            text, markup = pick_filler(self._last_filler.get(device_id, ""))
            state["filler"] = text
            self._last_filler[device_id] = text
            self._note("chat", f"⏳ '{text[:40]}'")
            print(f"[runtime] ⏳ brain over budget ({self.brain_budget_s:g}s) on "
                  f"{device_id} → filler: '{text}'", flush=True)
            _, scored = self._stage(text, turn_key=event_id, markup=markup)
            self._publish_chat(device_id, event_id, "router", text, markup,
                               result=ResultCode.REPLY_PENDING, chunk_num=0,
                               is_completed=False, scored=scored)
            self._maybe_synthesize(device_id, markup, event_id, chunk_num=0)
            return text

    # ---- one turn, streamed sentence by sentence ----
    def _handle_stream_turn(self, device_id, event_id, speech, turn, seq, stream,
                            app=None):
        """Answer a turn from an `Iterator[ReplyChunk]`, publishing as the model writes.

        Each finished sentence goes out immediately as `result=REPLY_PENDING` with its
        `chunk_num` (RemoteChat.proto field 22); the chunk the app marks `final` closes
        the sequence with `result=SUCCESS` + `consistency_control.is_completed`
        (field 18) — the contract's own "one event_id, several responses" shape
        (docs/reverse-engineering/protocol/remote-chat-protocol.md:26,:63). A turn that
        fits in ONE chunk is published exactly as it always was: no `chunk_num`, no
        `consistency_control`, so nothing downstream has to know about streaming.

        Latency cover: the filler timer is (re-)armed after every chunk, so a brain whose
        FIRST token is late gets a "let me think" line, and a stream that stalls
        mid-answer gets at most one more (`MAX_FILLERS_PER_TURN`). Fillers take the next
        `chunk_num` like any other chunk, so ordering on the wire is still total.

        Stale guard: a newer turn for this robot cancels the stream — we stop consuming
        it, close it, and publish nothing further for the old `event_id`.
        """
        state = {"lock": threading.Lock(), "done": False, "chunk": 0, "ans": 0,
                 "fillers": 0, "gen": 0, "timer": None}
        said, closed, failed = [], False, None
        acts: list = []                 # every action the stream asked for (e.g. <exit>)
        # Same queued hello as the non-streaming path: it takes chunk 0, the stream's own
        # sentences start at chunk 1. `ans` stays 0, so the answer keeps its mood.
        opener = self._speak_opener(device_id, event_id, seq)
        if opener:
            state["chunk"] = 1
            said.append(opener)
        self._arm_filler(device_id, event_id, seq, state)
        try:
            for chunk in stream:
                final = bool(getattr(chunk, "final", False))
                # Post-inference, per chunk: assessed BEFORE it is published, because a
                # streamed sentence is on the wire while the rest of the answer does not
                # exist yet. A blocked chunk is never spoken; the sequence closes on a
                # short safe line and the rest of the stream is cancelled.
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
                    self._note("chat", f"⏭️  cancelled a stale stream for {device_id}")
                    print(f"[runtime] ⏭️  turn {seq} superseded on {device_id}; "
                          f"cancelling the stream mid-answer", flush=True)
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
            # The stream ended (or died) without a final chunk. Nothing spoken yet →
            # the whole answer is still recoverable on the ordinary non-streaming path;
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
        self._note("chat", f"💬 '{speech[:30]}' → '{text[:40]}'")
        print(f"[runtime] 💬 {device_id}: '{speech[:40]}' → '{text[:60]}' "
              f"({state['chunk']} chunk(s))", flush=True)
        self._maybe_end_conversation(device_id, acts)

    def _safe_respond(self, turn, app=None):
        """One non-streamed answer, from the brain this turn resolved (`app_for`).

        `app=None` means the appliance's own — the only callers that pass nothing are the
        ones that have no device in hand."""
        try:
            return (app if app is not None else self.app).respond(turn)
        except Exception as e:
            print(f"[runtime] app.respond error: {e}", flush=True)
            return Reply(text="Hmm, let me think about that.")

    def _stage(self, text, obj=None, *, turn_key="", chunk_index=0, markup=None, **kw):
        """`(markup, scored)` for one spoken line — the seam's answer, plus the app's own.

        This is the single place a published turn becomes a *scored* turn. Before the
        behavior planner, `Reply.mood`/`dialog_act` were plumbed end to end and **no app
        ever set them**, and `ReplyChunk` did not have the fields at all — so a streamed
        answer could not carry scored output even in principle
        (docs/architecture/backlog/expressiveness.md §2.3, C4/C5). Now every path through
        `_publish_chat` that says words comes through here.

        Precedence, and the reason for it: **the app's own scoring wins**, field by field,
        and the seam fills in only what the app left None. A brain that knows its line is
        an `apology` is not second-guessed by a rule engine — but a brain that says
        nothing still ships a scored turn. Anything the app *did* say is a HINT into the
        planner as well, so the staged performance agrees with the wire fields rather than
        contradicting them, and an id it invents is dropped by `validate` like any other.

        `markup` is an app's authored markup: it is spoken verbatim (the idempotence rule),
        and the line is scored anyway.
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
        # The app's own values win — but they take the SAME positive list every other id
        # takes. An app is a brain by another name, and a brain may suggest, it may never
        # authorize: a `dialog_act` that is not one of the recovered 22 is dropped here
        # rather than forwarded onto `RemoteChatOutput`. (Found by mutation M28.)
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
        """One `ReplyChunk` (or `Reply`) onto the wire, with its chunk bookkeeping.

        `ann` is the chunk's index *within the answer* (fillers excluded). The markup
        floor emits the mood on index 0 only, so a streamed answer holds one face all
        the way through instead of flipping it every sentence — and a "let me think"
        line ahead of the answer does not cost the answer its mood.
        """
        markup, scored = self._stage(chunk.text, chunk, turn_key=event_id,
                                     chunk_index=n if ann is None else ann,
                                     markup=chunk.markup)
        result = getattr(chunk, "result_code", None)
        if result is None:
            result = ResultCode.SUCCESS if final else ResultCode.REPLY_PENDING
        # A one-chunk answer keeps the exact wire shape we have always sent: chunk 0 /
        # not-streaming is the proto default, so both fields stay off.
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
        """(Re)start the latency timer for a streaming turn. No-op once the turn is done
        or the per-turn filler budget is spent."""
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
        """The stream produced nothing for a whole budget → say something kind now.

        Fires for a late FIRST token and, once more at most, for a mid-answer stall.
        `gen` is the chunk counter this timer was armed against: if a chunk landed in the
        meantime the timer is stale and says nothing."""
        with state["lock"]:
            if state["done"] or state["gen"] != gen:
                return None                       # a chunk arrived — nothing to cover
            if state["fillers"] >= MAX_FILLERS_PER_TURN:
                return None
            if self._is_stale(device_id, seq):
                return None
            text, markup = pick_filler(self._last_filler.get(device_id, ""))
            self._last_filler[device_id] = text
            state["fillers"] += 1
            state["gen"] += 1
            state["timer"] = None
            n = state["chunk"]
            state["chunk"] = n + 1
            self._note("chat", f"⏳ '{text[:40]}'")
            print(f"[runtime] ⏳ stream quiet for {self.brain_budget_s:g}s on "
                  f"{device_id} → filler {state['fillers']}: '{text}'", flush=True)
            _, scored = self._stage(text, turn_key=event_id, chunk_index=n,
                                    markup=markup)
            self._publish_chat(device_id, event_id, "router", text, markup,
                               result=ResultCode.REPLY_PENDING, chunk_num=n,
                               is_completed=False, scored=scored)
            self._maybe_synthesize(device_id, markup, event_id, chunk_num=n)
        self._arm_filler(device_id, event_id, seq, state)   # another stall? one more line
        return text

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
        # `client-service-activity-log` is multiplexed by `subtopic`; the pull queries
        # ride subtopic="query" (mqtt-and-conversation.md:274). Older/looser senders omit
        # it, so a bare `query` still counts.
        if subtopic in (None, "", "query") and query in ("schedule", "mentor_behaviors",
                                                         "license"):
            # Answer as a CloudQueryResponse: echo `request_id` and key the payload by its
            # own proto field (schedule / mentor_behaviors / license_values) — see
            # build_activity_response.
            resp = build_activity_response(query, self._query_payload(device_id, query),
                                           request_id=data.get("request_id"))
            self._publish(f"/devices/{device_id}/commands/query_result", resp,
                          device_id=device_id, what="query_result")
            return resp
        # 🎭 The robot's own report of where it is in a telehealth session
        # (`TelehealthRobotEvent`, telehealth.md:88-91). READY / IN_SESSION / EXITING —
        # stored, never assumed: the card says "never reported" until this arrives.
        if subtopic == telehealth_seam.EVENT_SUBTOPIC:
            return self.ingest_telehealth_event(device_id, data)
        # The same topic also carries *reports*: `mentor_behavior` is what the child just
        # finished (or quit). Ingest it — that history is what stops the robot repeating
        # the same missions forever and lets FTUE end.
        if isinstance(data.get("mentor_behavior"), dict):
            return self.ingest_mentor_behavior(device_id, data)

    # ---- publish a chat response ----
    def _publish_chat(self, device_id, event_id, backend, text, markup="",
                      actions=None, end_turn=False, result=ResultCode.SUCCESS,
                      modules=None, mood=None, dialog_act=None,
                      chunk_num=None, is_completed=None, safety=None, scored=None,
                      subscribe=None):
        # Ask the robot to start pushing us its vision events, once per module. It rides
        # a spoken reply because that is the only cloud→robot message the contract gives
        # a `RemoteChatAction` to hang `EventSubscription` on — and it is attached only to
        # a plain, action-free closing reply so no reply that already carries a
        # launch/exit changes shape (see `_vision_subscription`).
        mine = None
        if (self.vision and modules is None and backend == "router" and not actions
                and result == ResultCode.SUCCESS and chunk_num in (None, 0)
                and self._vision_subscribed.get(device_id) !=
                    (getattr(self.robots.get(device_id), "module_id", None) or "")):
            mine = self._vision_subscription(device_id)
        # `subscribe` is what the *app* asked for on this reply (`Reply.subscribe` — in
        # practice a content pack's `subscribe` statement). It is merged INTO the
        # runtime's own list, never over it, and unlike `mine` it is not restricted to an
        # action-free reply: `MoxieGo`'s opening move is an `act` and a `subscribe`
        # together, so a gate that dropped one whenever the other was present would make
        # the pair unusable. `build_chat_response` already hangs the subscription on
        # `response_actions[0]` whatever else that entry carries.
        subscribe = self._merge_subscriptions(device_id, mine, subscribe)
        # `scored` is the seam's answer for this line (`_stage`); explicit mood/
        # dialog_act arguments still win, because a caller that passed one meant it.
        sc = dict(scored or {})
        resp = build_chat_response(event_id, text, markup, backend=backend,
                                   result=result, actions=actions, end_turn=end_turn,
                                   mood=mood or sc.get("mood"),
                                   dialog_act=dialog_act or sc.get("dialog_act"),
                                   modules=modules,
                                   chunk_num=chunk_num, is_completed=is_completed,
                                   safety=safety, subscribe_events=subscribe,
                                   mood_intensity=sc.get("mood_intensity"),
                                   emotion=sc.get("emotion"),
                                   signals=sc.get("signal"))
        self._publish(f"/devices/{device_id}/commands/remote_chat", resp,
                      device_id=device_id, what="remote_chat")
