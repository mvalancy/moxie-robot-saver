#!/usr/bin/env python3
"""
🤖 Virtual Moxie — a software-in-the-loop (SIL) robot.

Speaks the **exact MQTT protocol reverse-engineered from firmware
v3.6.4-Zephyr / OTA v24.10.803** (see docs/reverse-engineering/cloud-protocol.md),
so the server (mqtt/ supervisor + broker) can be built and tested with **no
hardware**. This is the "robot" half of the client/server pair, simulated.

What it does (the protocol round-trip a real Moxie performs):
  1. Connect to the broker with client_id ``d_<uuid>`` (the robot's device id form).
  2. Subscribe to ``/devices/{id}/config`` and ``/devices/{id}/commands/#``.
  3. Publish ``/devices/{id}/state`` {software_version: 24.10.803} — this is what
     makes the supervisor register the robot and push its config.
  4. Assert the pushed config has ``pairing_status == "paired"``.
  5. Publish a ``/devices/{id}/events/remote-chat`` prompt ("hello").
  6. Assert a ``/devices/{id}/commands/remote_chat`` reply with ``output.text`` arrives.
     One turn may answer with SEVERAL responses (a filler while the brain thinks, then
     the answer streamed sentence by sentence): they share an ``event_id``, carry a
     ``chunk_num``, and the last one is ``result=SUCCESS`` /
     ``consistency_control.is_completed``. We join them in order — see
     ``_on_chat_reply`` and docs/architecture/mqtt-and-conversation.md §4.5.

Exit code 0 = the full round-trip worked. Used by the CI workflow (sim/ci/ci.yml;
install to .github/workflows/ to run it on GitHub)
and by ``sim/run_smoke.sh`` locally.

Usage:
  python3 sim/virtual_moxie.py --host 127.0.0.1 --port 1883 --timeout 15
  python3 sim/virtual_moxie.py --expect-unpaired    # assert the device allowlist gates us
"""
from __future__ import annotations
import argparse, json, re, sys, threading, time, uuid

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("virtual_moxie needs paho-mqtt:  pip install 'paho-mqtt>=2.0'")

FIRMWARE = "24.10.803"           # the analyzed build; robot reports this in /state

#: The scored half of `RemoteChatOutput` — what the line MEANS and how it is performed
#: (docs/architecture/ai-seam.md §2; backlog/expressiveness.md §2.3 C1/C3). A robot that
#: only ever checked `output.text` cannot tell a scored appliance from an unscored one, so
#: `--expect-scored` asserts these arrive on **every** response of a turn, streamed chunks
#: included. `signals` is plural on the wire (the field is `repeated`) even though the
#: planner's own dict spells it `signal`.
SCORED_FIELDS = ("mood", "mood_intensity", "dialog_act", "emotion", "signals")

#: The prompt the standing smoke speaks. A constant because `--reject-echo` has to
#: reconstruct the *exact* answer the built-in echo app would have given to it, and a
#: prompt that drifted away from that reconstruction would silently disarm the check.
SMOKE_PROMPT = "hello Moxie"

#: What `moxie_sdk/apps/echo_app.py` answers with — the no-brain app every smoke has run
#: against since the first one. `--reject-echo` exists because a round-trip that is real
#: at every other layer (real broker, real supervisor, real TTS, real robot) proves
#: nothing about the AI seam while this string is the reply.
ECHO_TEMPLATE = "You said: {speech}"


def is_echo_reply(text: str, prompt: str = SMOKE_PROMPT) -> bool:
    """Whether `text` is the built-in echo app's verbatim answer to `prompt`.

    Markup is stripped before comparing. `MOXIE_EXPRESSIVE` can dress a reply in
    `<mark …>` tags on the way out, and an echoed line wearing markup is still an echoed
    line — the claim under test is *no model was consulted*, not how the words were
    decorated. Anything else — including an empty reply — is not the echo app, and is
    left for the assertions that already cover it.
    """
    bare = re.sub(r"<[^>]*>", "", text or "").strip()
    return bare == ECHO_TEMPLATE.format(speech=prompt).strip()


#: 🎬 The action verbs this client implements — `RemoteChatAction.ActionID` as our server
#: can spell it (`mqtt/moxie_sdk/types.py::ActionType`), and exactly the five
#: `sim/web/bridge.js::ACTION_KINDS` implements. Written out as a literal ON PURPOSE: the
#: SIL robot decodes the wire itself, the way firmware does, and never imports the server
#: SDK it exists to test — the same rule `QUERY_FIELD` below is written under.
#: `sim/tests/test_sim_client_parity.py` asserts the three lists agree, so the duplication
#: cannot drift.
#:
#: ⚠️ Two of these are **not** names in the recovered `ActionID` enum (`launch`,
#: `launch_if_confirmed`, `exit_module`, `request_next`, `abort_module`, `execute`,
#: `sleep`, `tangent` — proto-catalog.md:2091): `exit` should be `exit_module`, and
#: `enable_qr` is not a verb at all (the contract spells it `execute` +
#: `function_id: "eb_enable_qr"`). This client decodes what our server actually sends,
#: which is what "interchangeable clients" means; correcting the *wire* is a contract
#: change filed against `build_chat_response`, not a harness change — see
#: docs/architecture/backlog/qr-launch-cards.md §P0-a and §7 R3.
ACTION_KINDS = ("launch", "exit", "sleep", "enable_qr", "execute")


class VirtualMoxie:
    def __init__(self, host: str, port: int, device_id: str | None = None,
                 timeout: float = 15.0, verbose: bool = True, expect_tts: bool = False,
                 expect_scored: bool = False, reject_echo: bool = False):
        self.host, self.port, self.timeout = host, port, timeout
        self.device_id = device_id or f"d_{uuid.uuid4()}"
        self.verbose = verbose
        self.expect_tts = expect_tts        # also assert a CloudTTSResponse (audio) arrives
        self.expect_scored = expect_scored  # ...and that every response carries its score
        self.reject_echo = reject_echo      # ...and that a real brain, not `echo`, wrote it
        self.got_config = threading.Event()
        self.got_reply = threading.Event()
        self.got_tts = threading.Event()
        self.got_query = threading.Event()
        self.config_payload: dict | None = None
        self.reply_payload: dict | None = None   # the FINAL RemoteChatResponse of a turn
        self.reply_text: str = ""                # every chunk of that turn, in order
        self._chunks: dict[str, dict[int, str]] = {}   # event_id -> {chunk_num: text}
        #: Every RemoteChatResponse of the CURRENT turn, verbatim and in arrival order.
        #: `reply_payload` is only the closing one, so a claim about what a *streamed*
        #: answer carried has nowhere else to look.
        self.chat_payloads: list = []
        self.query_results: dict = {}       # CloudQuery name -> last CloudQueryResponse
        self.spoke: dict | None = None      # last decoded CloudTTSResponse (audio playback)
        self.face_replies: list = []        # what the server answered each vision event
        # 🎭 telehealth: every TelehealthRobotCommand this robot was sent, in order, plus
        # the state we have told the cloud we are in (READY → IN_SESSION → EXITING).
        self.got_telehealth = threading.Event()
        self.telehealth: list = []
        self.telehealth_state: str = ""
        # 🎬 What the cloud's `response_actions` have DONE to this robot, in the same
        # shape `sim/web/bridge.js::actionStats()` reports — see `_apply_action`. Client
        # lifetime, not per-turn: the module the cloud last put us in outlives the turn
        # that put us there, exactly as it does on the browser SIM.
        self.got_action = threading.Event()
        self.actions: dict = {
            "applied": [],          # [{action, module_id, content_id, function, args}]
            "unknown": 0,           # verbs this client does not implement (skipped)
            "module_id": "", "content_id": "",   # the module the cloud last put us in
            "launches": 0, "exits": 0,
            "asleep": False, "qr_enabled": False,
            "subscribed": [],       # event_subscription.active, as last asked for
            "last": "",
        }
        self.errors: list[str] = []
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.device_id)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def log(self, *a):
        if self.verbose:
            print("[virtual-moxie]", *a, flush=True)

    # -- topics --
    @property
    def t_state(self):    return f"/devices/{self.device_id}/state"
    @property
    def t_config(self):   return f"/devices/{self.device_id}/config"
    @property
    def t_commands(self): return f"/devices/{self.device_id}/commands/#"
    def t_event(self, name): return f"/devices/{self.device_id}/events/{name}"

    def _on_connect(self, c, u, flags, rc, props=None):
        self.log(f"connected to broker rc={rc} as {self.device_id}")
        c.subscribe(self.t_config)
        c.subscribe(self.t_commands)

    def _on_message(self, c, u, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8", "replace"))
        except Exception:
            payload = {"_raw": msg.payload[:80].hex()}
        topic = msg.topic
        if topic == self.t_config:
            self.config_payload = payload
            self.log(f"← config: pairing_status={payload.get('pairing_status')!r}")
            self.got_config.set()
        elif topic.endswith("/commands/remote_chat"):
            self._on_chat_reply(payload)
        elif topic.endswith("/commands/tts"):
            self._play_tts(payload)
        elif topic.endswith("/commands/query_result"):
            self._on_query_result(payload)
        elif topic.endswith("/commands/telehealth"):
            self._on_telehealth(payload)
        elif "/commands/" in topic:
            self.log(f"← {topic.split('/commands/')[-1]}: {str(payload)[:60]}")

    def _on_chat_reply(self, payload):
        """Accumulate one turn's answer, which may arrive as SEVERAL responses.

        The contract lets one ``event_id`` be answered by more than one
        ``RemoteChatResponse``: ``result=REPLY_PENDING`` (ResultCode 9) means "more chunks
        to come", ``chunk_num`` (field 22) orders them, and
        ``consistency_control.is_completed`` (field 18) marks the last one — see
        docs/architecture/mqtt-and-conversation.md §4.5. Our server uses that to speak a
        filler while a slow brain thinks, and to stream an answer sentence by sentence.

        So a real client cannot treat the FIRST reply as the answer. This joins the chunks
        of an event in ``chunk_num`` order and only wakes the waiter on the closing chunk
        (a terminal ``result``, or ``is_completed``). ``reply_payload`` stays the final
        response; ``reply_text`` is the whole thing the child heard.
        """
        event_id = payload.get("event_id") or ""
        chunk_num = payload.get("chunk_num")
        text = (payload.get("output") or {}).get("text", "")
        self.chat_payloads.append(payload)
        # …and then what the cloud asked this ROBOT to DO. Applied per RESPONSE, not per
        # turn — a streamed answer is several publishes and an action may ride any of
        # them — and BEFORE `got_reply` is set, so a waiter that wakes on the reply is
        # already looking at settled action state rather than racing it.
        self._on_actions(payload)
        parts = self._chunks.setdefault(event_id, {})
        parts[int(chunk_num) if chunk_num is not None else 0] = text
        result = payload.get("result")
        completed = bool((payload.get("consistency_control") or {}).get("is_completed"))
        pending = result == "REPLY_PENDING" and not completed
        if pending:
            self.log(f"← remote_chat chunk {chunk_num}: {text[:60]!r} (more to come)")
            return
        self.reply_payload = payload
        self.reply_text = " ".join(parts[k] for k in sorted(parts) if parts[k]).strip()
        self.log(f"← remote_chat reply ({len(parts)} chunk(s)): {self.reply_text[:60]!r}")
        self.got_reply.set()

    def _reset_turn(self):
        """Forget the previous turn's chunks before sending the next prompt."""
        self.got_reply.clear()
        self.reply_payload = None
        self.reply_text = ""
        self._chunks.clear()
        self.chat_payloads = []
        # Only the EDGE is per-turn. `self.actions` is client state (which module we are
        # in, whether we are asleep, what we are subscribed to) and a new prompt does not
        # undo it — the browser SIM's `actionState` has the same lifetime.
        self.got_action.clear()

    # -- 🎬 response_actions: the brain drives this robot, not just its mouth --
    #
    # `RemoteChatResponse.response_actions[]` is a list of `RemoteChatAction`s
    # (docs/reverse-engineering/protocol/remote-chat-protocol.md §"RemoteChatAction — the
    # brain drives navigation"): the brain can start a content module, leave one, put
    # Moxie to sleep, run a named on-robot function, and subscribe to the robot's own
    # perception events. `docs/architecture/ai-seam.md` §2 and
    # `mqtt/moxie_sdk/wire.py::build_chat_response` are our server's half of it.
    #
    # Until this landed the SIL robot read `output.text` and the audio and **ignored the
    # actions completely**, so no test could assert that a robot RECEIVED AND ACTED ON a
    # launch — only that the runtime published one. That was DoD criterion 4's
    # ("interchangeable clients") one untrue clause, because the browser SIM
    # (`sim/web/bridge.js::applyAction`) has acted on them since PR #52.
    #
    # WHAT THIS DELIBERATELY DOES NOT DO. A stub that RECORDS is right here; a stub that
    # pretends to run a module is not. So:
    #   * `launch` does not start anything — there is no content engine on this client,
    #     and inventing one would make the SIL robot lie about what a real robot did.
    #   * `execute` is recorded by name and never called. The contract says the result
    #     comes back next turn in `RemoteChatRequest.execute_returns[]`; we do not send
    #     that, because we would have to invent a return value for a function we did not
    #     run. Named as a gap rather than faked.
    #   * `sleep` records that we were told to sleep; it does not stop the client. The
    #     corpus describes no wake handshake this robot could then honour.
    #   * `enable_qr` records that the scanner was armed. A headless client has no camera.
    # Everything it DOES do mirrors `bridge.js::applyAction` state for state, which is the
    # whole point: the two clients must agree about what an action meant.

    def _on_actions(self, payload: dict):
        """Consume one response's `response_actions` — the mirror of `bridge.js::handleActions`.

        The plural `response_actions[]` is the contract's list; a legacy singular
        `response_action` mirrors `response_actions[0]`
        (docs/architecture/mqtt-and-conversation.md §4.1), so it is read **only** when the
        plural is absent — otherwise one action would fire twice.

        An entry with no `action` is legal and is not an error: that is the shape
        `build_chat_response` sends when a brain is only subscribing to events. Nothing
        here raises — a future server verb must not be able to break an old client's turn.
        """
        entries = payload.get("response_actions")
        if not isinstance(entries, list):
            single = payload.get("response_action")
            entries = [single] if single else []
        for entry in entries:
            if not isinstance(entry, dict):
                self.actions["unknown"] += 1        # junk on the wire, counted not raised
                continue
            self._note_subscription(entry.get("event_subscription"))
            if not entry.get("action"):
                continue                            # subscription-only entry
            try:
                self._apply_action(entry)
            except Exception as e:                  # never let an action break the turn
                self.actions["unknown"] += 1
                self.log(f"🎬 action failed: {e}")

    def _note_subscription(self, sub):
        """`RemoteChatAction.EventSubscription{clear, active[]}` — the brain asking this
        robot to push it perception events (remote-chat-protocol.md:103-106)."""
        if not isinstance(sub, dict):
            return
        if sub.get("clear"):
            self.actions["subscribed"] = []
        for name in sub.get("active") or []:
            if name not in self.actions["subscribed"]:
                self.actions["subscribed"].append(name)
        self.log(f"🎬 event subscription: {', '.join(self.actions['subscribed']) or '(none)'}")

    @staticmethod
    def _action_args(entries):
        """`RemoteChatAction.action_args` — `repeated ActionArgsEntry{key, value}`, proto
        field 10 — as the `{key: value}` mapping it encodes. `None` when the field is
        absent or unreadable, so the caller falls through to its next spelling rather than
        recording an empty dict as if the brain had sent one."""
        if not isinstance(entries, list):
            return None
        pairs = [(e.get("key"), e.get("value")) for e in entries if isinstance(e, dict)]
        return {str(k): v for k, v in pairs if k is not None} or None

    def _apply_action(self, entry: dict) -> bool:
        """Act on one `RemoteChatAction`. Returns False for a verb we do not implement.

        `function` is read from `function_id` first and the SIM's `function` second.
        `RemoteChat.proto`:255-281 names the fields `function_id` (7) / `function_args`
        (8) / `action_args` (10), and that is what a real robot decodes;
        `sim/web/bridge.js`:258 reads `entry.function`.

        **Since 2026-09-04 our own server emits the contract's spelling** —
        `wire.py::encode_action` sends `function_id` plus `function_args` (a list) or
        `action_args` (a dict), so an `execute` we send arrives NAMED and this client
        records the name it was given. All four spellings are still accepted, because a
        client that only understood the one server it was written against would not be a
        client. An `execute` with nothing to read still records `""` rather than a guess.

        **It records; it does not run.** No function is called and no
        `RemoteChatRequest.execute_returns[]` is published — we would have to invent a
        return value for a function this headless client does not have.
        """
        kind = str(entry.get("action") or "").lower()
        module_id = entry.get("module_id") or ""
        content_id = entry.get("content_id") or ""
        function = entry.get("function_id") or entry.get("function") or ""
        args = entry.get("function_args")
        if args is None:
            args = self._action_args(entry.get("action_args"))
        if args is None:
            args = entry.get("args")
        if kind not in ACTION_KINDS:
            self.actions["unknown"] += 1
            self.log(f"🎬 ignored unknown action {entry.get('action')!r}")
            return False
        if kind == "launch":
            # We are now IN that module as far as this client is concerned. Nothing is
            # started; the navigation state is the honest part and the part a test asserts.
            self.actions["module_id"] = module_id
            self.actions["content_id"] = content_id
            self.actions["asleep"] = False
            self.actions["launches"] += 1
            self.log(f"🎬 launch {module_id}" + (f":{content_id}" if content_id else ""))
        elif kind == "exit":
            self.actions["module_id"] = ""
            self.actions["content_id"] = ""
            self.actions["exits"] += 1
            self.log("🎬 exit")
        elif kind == "sleep":
            self.actions["asleep"] = True
            self.log("🎬 sleep")
        elif kind == "enable_qr":
            self.actions["qr_enabled"] = True
            self.log("🎬 QR scanning on")
        elif kind == "execute":
            self.log(f"🎬 execute {function or '(unnamed)'}")
        self.actions["last"] = kind
        self.actions["applied"].append({"action": kind, "module_id": module_id,
                                        "content_id": content_id, "function": function,
                                        "args": args if args is not None else []})
        if len(self.actions["applied"]) > 40:       # bounded, like the browser SIM's
            self.actions["applied"].pop(0)
        self.got_action.set()
        return True

    def action_stats(self) -> dict:
        """What the cloud's actions did to this robot — the same keys, in the same
        meanings, as `sim/web/bridge.js::actionStats()`. Tests assert this; the parity of
        the two shapes is asserted in `sim/tests/test_sim_client_parity.py`."""
        a = self.actions
        return {"applied": [dict(x) for x in a["applied"]], "unknown": a["unknown"],
                "module_id": a["module_id"], "content_id": a["content_id"],
                "launches": a["launches"], "exits": a["exits"], "asleep": a["asleep"],
                "qr_enabled": a["qr_enabled"], "subscribed": list(a["subscribed"]),
                "last": a["last"]}

    def _play_tts(self, payload):
        """Consume a CloudTTSResponse: decode the audio buffer + marks and 'play' it.
        A real robot renders audio to the speaker; the headless SIM records that Moxie
        spoke (bytes + sample rate) so scenarios/tests can assert the voice reached it.
        Decodes the wire shape directly (base64 AudioBuffer) — the SIM is a protocol
        client and stays independent of the server SDK, like a real robot's firmware."""
        import base64
        audio_obj = (payload or {}).get("audio") or {}
        try:
            audio = base64.b64decode(audio_obj.get("buffer") or "")
        except Exception as e:
            self.errors.append(f"tts decode failed: {e}")
            return
        rate = int(audio_obj.get("sample_rate", 24000) or 24000)
        channels = int(audio_obj.get("channels", 1) or 1)
        marks = payload.get("marks") or []
        self.spoke = {"audio": audio, "sample_rate": rate, "channels": channels,
                      "marks": marks, "event_id": payload.get("event_id", "")}
        secs = len(audio) / (2 * channels * rate) if rate else 0.0
        self.log(f"🔊 spoke {len(audio)} B @ {rate} Hz (~{secs:.2f}s, {len(marks)} marks)")
        self.got_tts.set()

    # -- content queries (CloudQueryRequest / CloudQueryResponse) --
    # The CloudQueryResponse field each answer is keyed under, per the recovered
    # embodied.logging.CloudQueryResponse (docs/reverse-engineering/protocol/
    # recovered-proto/embodied/logging/Cloud.proto:310-352). Duplicated here on purpose:
    # the SIL robot decodes the wire itself, like real firmware, and never imports the
    # server SDK it is meant to be testing.
    QUERY_FIELD = {"idf": "idf_values", "license": "license_values",
                   "schedule": "schedule", "contexts": "contexts",
                   "context_store": "versioned_contexts",
                   "mentor_behaviors": "mentor_behaviors", "remote_lines": "remote_lines"}

    def _on_query_result(self, payload):
        """Consume a CloudQueryResponse off /commands/query_result."""
        query = (payload or {}).get("query", "")
        field = self.QUERY_FIELD.get(query, "")
        value = (payload or {}).get(field)
        self.query_results[query] = {"request_id": payload.get("request_id"),
                                     "field": field, "value": value, "raw": payload}
        size = len(value) if isinstance(value, (list, dict)) else value
        self.log(f"← query_result {query!r}: {field}={size if size is not None else 'MISSING'}")
        self.got_query.set()

    def send_query(self, query: str) -> str:
        """Publish a CloudQueryRequest (Cloud.proto:292-305) on the activity-log topic —
        exactly how the robot pulls its schedule/history at session start
        (cloud-protocol.md:172: `client-service-activity-log`, `subtopic:"query"`)."""
        request_id = str(uuid.uuid4())
        self.client.publish(self.t_event("client-service-activity-log"), json.dumps(
            {"timestamp": int(time.time() * 1000), "subtopic": "query", "query": query,
             "request_id": request_id, "auid": self.device_id,
             "software_version": FIRMWARE, "module_name": "virtual-moxie"}))
        self.log(f"→ events/client-service-activity-log query={query!r} id={request_id}")
        return request_id

    def report_mentor_behavior(self, mbh: dict):
        """Report a finished activity: an ActivityUpdate whose `mentor_behavior` field
        (Cloud.proto:241) carries the MentorBehavior record (MentorBehavior.proto:26-36)."""
        self.client.publish(self.t_event("client-service-activity-log"), json.dumps(
            {"timestamp": int(time.time() * 1000), "mentor_behavior": mbh,
             "software_version": FIRMWARE, "module_name": "virtual-moxie"}))
        self.log(f"→ mentor_behavior report: {mbh.get('module_id')} {mbh.get('action')}")

    def query(self, name: str, timeout: float | None = None):
        """Send one query and wait for its answer. Returns the decoded value or None."""
        self.got_query.clear()
        self.query_results.pop(name, None)
        request_id = self.send_query(name)
        deadline = time.time() + (timeout if timeout is not None else self.timeout)
        while time.time() < deadline:
            if self.got_query.wait(0.25):
                self.got_query.clear()
                got = self.query_results.get(name)
                if got:
                    if got["request_id"] != request_id:
                        self.errors.append(
                            f"{name}: request_id {got['request_id']!r} != sent {request_id!r}")
                    return got["value"]
        self.errors.append(f"no query_result for {name!r} within timeout")
        return None

    def run_queries(self, queries, report=None) -> bool:
        """Connect, announce, optionally report a MentorBehavior, then run the queries.
        Results land in self.query_results. Returns False if anything went unanswered."""
        self.client.connect(self.host, self.port, 30)
        self.client.loop_start()
        try:
            self.client.publish(self.t_state, json.dumps(
                {"software_version": FIRMWARE, "state": "config"}))
            # A config push is nice-to-have here, not required: a server only re-pushes
            # config for a robot it hasn't seen, and a real Moxie re-queries its schedule
            # every session regardless. Queries are what this run is testing.
            if not self.got_config.wait(min(3.0, self.timeout)):
                self.log("(no config push — already-known robot; continuing to queries)")
            if report:
                self.report_mentor_behavior(report)
                time.sleep(1.0)              # let the server ingest before we ask for it
            ok = True
            for name in queries:
                if self.query(name) is None:
                    ok = False
            return ok and not self.errors
        finally:
            self.client.loop_stop()
            self.client.disconnect()

    # -- vision: the robot's own eyes (docs/architecture/vision.md) --
    #
    # The stock robot runs vision ON-DEVICE and emits semantic events only — no pixels,
    # no bounding boxes (vision.md §1.1). A subscribed event is delivered to the brain as
    # the `speech` of an ordinary RemoteChatRequest ("instead of the modules receiving
    # something the user said, it receives a special event string like `eb-found-face`" —
    # RemoteModuleAPI §Event Handling), so the SIL robot publishes it on exactly the topic
    # and in exactly the envelope it publishes a child's utterance in. Nothing new on the
    # wire: that IS the protocol-faithful shape.
    FACE_EVENTS = {"found": "eb-found-face", "lost": "eb-lost-target"}

    def send_face_event(self, kind: str, input_vars: dict | None = None) -> str:
        """Publish one vision event. `kind` is `found`/`lost` or a raw `eb-*` name."""
        name = self.FACE_EVENTS.get(kind, kind)
        event_id = str(uuid.uuid4())
        payload = {"event_id": event_id, "command": "prompt", "backend": "router",
                   "speech": name, "module_name": "virtual-moxie"}
        if input_vars:
            payload["input_vars"] = input_vars
        self.client.publish(self.t_event("remote-chat"), json.dumps(payload))
        self.log(f"→ events/remote-chat vision event: {name!r}")
        return event_id

    def run_face_events(self, kinds, gap: float = 0.0) -> bool:
        """Announce, then play a list of vision events, asserting the server answers each.

        A server that has nothing to say answers `NOREPLY_ACK` (ResultCode 6, "acknowledge
        only, no spoken line") — the contract still requires *a* response, because "the
        remote module must produce some response for this input to continue the
        interaction". A hello is a `SUCCESS` with real `output.text`. Both count as
        answered; `self.face_replies` records which was which."""
        self.face_replies = []
        self.client.connect(self.host, self.port, 30)
        self.client.loop_start()
        try:
            self.client.publish(self.t_state, json.dumps(
                {"software_version": FIRMWARE, "state": "config"}))
            if not self.got_config.wait(min(5.0, self.timeout)):
                self.log("(no config push — already-known robot; continuing)")
            for i, kind in enumerate(kinds):
                if i and gap:
                    time.sleep(gap)
                self._reset_turn()
                self.send_face_event(kind)
                if not self.got_reply.wait(self.timeout):
                    self.errors.append(f"{kind!r}: no response to the vision event")
                    continue
                resp = self.reply_payload or {}
                text = (resp.get("output") or {}).get("text", "")
                self.face_replies.append({"kind": kind, "result": resp.get("result"),
                                          "text": text, "event_id": resp.get("event_id")})
                self.log(f"   {kind}: result={resp.get('result')} text={text[:60]!r}")
            return not self.errors
        finally:
            self.client.loop_stop()
            self.client.disconnect()

    # -- the pairing gate: what a NOT-permitted robot is served --
    def run_unpaired(self) -> bool:
        """Announce ourselves and assert we are treated as **pending**.

        The supervisor's device allowlist (`fleet/permits.json`) is closed by default, so
        a robot it has never been told about must receive the minimal config: a
        `pairing_status` that is not `"paired"` and — the point of the whole gate — **no
        `child_pii`**. Prints the document it got, so a live check can show it verbatim.
        """
        self.client.connect(self.host, self.port, 30)
        self.client.loop_start()
        try:
            self.client.publish(self.t_state, json.dumps(
                {"software_version": FIRMWARE, "state": "config"}))
            self.log(f"→ state (software_version={FIRMWARE})")
            if not self.got_config.wait(self.timeout):
                self.errors.append("no config pushed within timeout")
                return False
            cfg = self.config_payload or {}
            print(json.dumps(cfg, indent=2, sort_keys=True))
            if cfg.get("pairing_status") == "paired":
                self.errors.append("expected an un-paired config, got pairing_status='paired'")
            if "child_pii" in cfg:
                self.errors.append("LEAK: an unpermitted device was sent child_pii")
            return not self.errors
        finally:
            self.client.loop_stop()
            self.client.disconnect()

    # -- 🎭 telehealth / "Be Moxie": the operator drives the body --
    #
    # The recovered `embodied.telehealth.TeleHealth.proto`
    # (docs/reverse-engineering/protocol/telehealth.md) puts a remote human where the
    # on-device BRAIN normally is: the cloud sends `TelehealthRobotCommand` on
    # `commands/telehealth` and the robot reports its `RobotState` back on the
    # `client-service-activity-log` `telehealth` subtopic. This SIL robot plays both
    # halves of that faithfully — it decodes the JSON itself, like firmware, and never
    # imports the server SDK it is testing — while a *third* half, the operator, is driven
    # over the supervisor's localhost status HTTP, which is exactly what the console does.

    def _on_telehealth(self, payload):
        """Consume one `TelehealthRobotCommand` and answer the way the protocol says."""
        message = (payload or {}).get("message") or {}
        action = str(message.get("action") or "")
        output = message.get("output") or {}
        self.telehealth.append({"action": action, "session_id": message.get("session_id", ""),
                                "text": output.get("text", ""),
                                "markup": output.get("markup", ""),
                                "output": "output" in message})
        if action == "START_SESSION":
            self.report_telehealth_state("IN_SESSION", message.get("session_id", ""))
        elif action == "END_SESSION":
            # EXITING then READY — the teardown the protocol page draws (:66-79).
            self.report_telehealth_state("EXITING", message.get("session_id", ""))
            self.report_telehealth_state("READY", "")
        elif action == "PLAY_OUTPUT":
            self.log(f"🎭 speaks the operator's line: {output.get('text', '')[:60]!r}")
        self.log(f"← telehealth {action}"
                 + (f" session={message.get('session_id')}" if message.get("session_id") else ""))
        self.got_telehealth.set()

    def report_telehealth_state(self, state: str, session_id: str = ""):
        """Publish a `TelehealthRobotEvent` on the activity log's `telehealth` subtopic."""
        self.telehealth_state = state
        self.client.publish(self.t_event("client-service-activity-log"), json.dumps(
            {"subtopic": "telehealth",
             "message": {"timestamp": int(time.time() * 1000), "state": state,
                         "session_id": session_id, "action": "UPDATE_STATE",
                         "software_version": FIRMWARE, "module_name": "virtual-moxie"}}))
        self.log(f"→ telehealth state {state}")

    def _await_telehealth(self, action: str, timeout: float | None = None):
        """Wait for one action to arrive on `commands/telehealth`. Returns it or None."""
        deadline = time.time() + (timeout if timeout is not None else self.timeout)
        while time.time() < deadline:
            for rec in self.telehealth:
                if rec["action"] == action:
                    return rec
            self.got_telehealth.wait(0.25)
            self.got_telehealth.clear()
        self.errors.append(f"no telehealth {action} within timeout")
        return None

    def run_telehealth(self, status_url: str, line: str = "Hello from the operator.",
                       mood: str = "happy", intensity: int = 2) -> bool:
        """The end-to-end puppet check: an operator drives this robot from the console's
        own seam and the robot speaks their line.

        `status_url` is the supervisor's localhost status server (`http://127.0.0.1:PORT`)
        — the same endpoint `server/moxie_server/main.py` proxies, so this exercises the
        real verb chain rather than a test double:

            enable → start → speak(mood, intensity) → GET → interrupt → end

        Asserts the recovered wire at every step: `PLAY_OUTPUT` carries the operator's text
        AND markup; `INTERRUPT` carries **no** `output` at all; the pushed `/config` really
        did flip `moxie_mode` to `TELEHEALTH`; and the supervisor's own `/telehealth` view
        shows the state THIS robot reported plus the operator's line in the transcript.
        """
        import urllib.error
        import urllib.request
        from urllib.parse import quote

        base = status_url.rstrip("/")

        def call(payload=None):
            url = f"{base}/telehealth?device_id={quote(self.device_id)}"
            data = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(
                url, data=data, method="POST" if data else "GET",
                headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return json.loads(r.read().decode()), 200
            except urllib.error.HTTPError as e:
                return json.loads(e.read().decode() or "{}"), e.code

        self.client.connect(self.host, self.port, 30)
        self.client.loop_start()
        try:
            self.client.publish(self.t_state, json.dumps(
                {"software_version": FIRMWARE, "state": "config"}))
            if not self.got_config.wait(self.timeout):
                self.errors.append("no config pushed within timeout")
                return False
            self.report_telehealth_state("READY")

            # ASSUMPTION B1 made observable: the re-pushed config really carries the mode.
            # Armed BEFORE the call — the supervisor publishes the new config before its
            # HTTP reply comes back, so clearing afterwards would drop the very push we
            # are waiting for.
            self.got_config.clear()
            out, code = call({"action": "enable"})
            if code != 200 or not out.get("ok"):
                self.errors.append(f"enable failed ({code}): {out.get('reason') or out}")
                return False
            if not self.got_config.wait(5.0):
                self.errors.append("enable did not re-push /config")
            elif (self.config_payload or {}).get("moxie_mode") != "TELEHEALTH":
                self.errors.append(
                    f"config moxie_mode={(self.config_payload or {}).get('moxie_mode')!r}, "
                    "expected 'TELEHEALTH'")

            out, code = call({"action": "start"})
            started = self._await_telehealth("START_SESSION")
            if not started:
                return False
            session_id = started["session_id"]
            if not session_id:
                self.errors.append("START_SESSION carried no session_id")

            out, code = call({"action": "speak", "text": line, "mood": mood,
                              "intensity": intensity})
            if code != 200 or not out.get("ok"):
                self.errors.append(f"speak failed ({code}): {out.get('reason') or out}")
                return False
            spoken = self._await_telehealth("PLAY_OUTPUT")
            if not spoken:
                return False
            if spoken["text"] != line:
                self.errors.append(f"PLAY_OUTPUT text {spoken['text']!r} != {line!r}")
            if not spoken["markup"]:
                self.errors.append("PLAY_OUTPUT carried no markup")
            if spoken["session_id"] != session_id:
                self.errors.append("PLAY_OUTPUT session_id does not match the session")

            view, code = call()
            if code != 200 or view.get("state") != "IN_SESSION":
                self.errors.append(
                    f"supervisor /telehealth state={view.get('state')!r}, expected IN_SESSION")
            said = [t for t in (view.get("transcript") or []) if t.get("who") == "operator"]
            if not any(t.get("text") == line for t in said):
                self.errors.append("the operator's line is not in the supervisor transcript")

            call({"action": "interrupt"})
            cut = self._await_telehealth("INTERRUPT")
            if cut and cut["output"]:
                self.errors.append("INTERRUPT must carry no output")

            call({"action": "end"})
            if not self._await_telehealth("END_SESSION"):
                return False
            time.sleep(0.5)                     # let our EXITING → READY reports land
            call({"action": "disable"})
            return not self.errors
        finally:
            self.client.loop_stop()
            self.client.disconnect()

    # -- the scripted round-trip --
    def run_smoke(self) -> bool:
        self.client.connect(self.host, self.port, 30)
        self.client.loop_start()
        try:
            # 1) announce presence via /state (registers us + triggers config push)
            self.client.publish(self.t_state, json.dumps(
                {"software_version": FIRMWARE, "state": "config"}))
            self.log(f"→ state (software_version={FIRMWARE})")

            # 2) wait for config, assert paired
            if not self.got_config.wait(self.timeout):
                self.errors.append("no config pushed within timeout")
                return False
            ps = (self.config_payload or {}).get("pairing_status")
            if ps != "paired":
                self.errors.append(f"config pairing_status={ps!r}, expected 'paired'")
                return False

            # 3) send a remote-chat prompt
            event_id = str(uuid.uuid4())
            self.client.publish(self.t_event("remote-chat"), json.dumps(
                {"event_id": event_id, "command": "prompt", "backend": "router",
                 "speech": SMOKE_PROMPT}))
            self.log(f"→ events/remote-chat prompt: {SMOKE_PROMPT!r}")

            # 4) wait for the reply, assert it has text
            if not self.got_reply.wait(self.timeout):
                self.errors.append("no remote_chat reply within timeout")
                return False
            text = self.reply_text or ((self.reply_payload or {}).get("output") or {}).get("text", "")
            if not text:
                self.errors.append("remote_chat reply had empty output.text")
                return False

            # 4b) (optional) assert a real brain — not the built-in echo app — wrote it.
            # Every other layer of this smoke has always been real (a broker on a socket,
            # the supervisor process, synthesized audio, this robot decoding the wire);
            # the brain was the one mock left, and `MOXIE_APP=echo` was pinned in
            # `run_smoke.sh` with no way to change it. `--reject-echo` is what makes
            # `run_smoke.sh --live-brain` a claim about the AI seam rather than about
            # five layers around a stub. See docs/architecture/implementation-plan.md,
            # Definition of done #1.
            if self.reject_echo:
                if is_echo_reply(text, SMOKE_PROMPT):
                    self.errors.append(
                        f"the reply is the echo app's own answer ({text!r}) — this run "
                        f"was supposed to be driven by a real brain, so the supervisor "
                        f"is still on MOXIE_APP=echo (or fell back to it)")
                    return False
                self.log(f"🧠 live brain reply: {text!r}")

            # 5) (optional) assert the appliance SCORED the line it sent us. The five
            # fields are the difference between a speaker and a performance, and until
            # now nothing on the standing smoke path looked at them — a regression that
            # emptied `dialog_act` on the wire would have kept this smoke green.
            if self.expect_scored and not self.check_scored():
                return False

            # 6) (optional) assert the server voice reached us as audio on /commands/tts
            if self.expect_tts:
                if not self.got_tts.wait(self.timeout):
                    self.errors.append("expected a CloudTTSResponse (tts) but none arrived")
                    return False
                if not (self.spoke and self.spoke.get("audio")):
                    self.errors.append("tts arrived but carried no audio")
                    return False
            return True
        finally:
            self.client.loop_stop()
            self.client.disconnect()

    def check_scored(self) -> bool:
        """Every response of the last turn carries every scored field. Logs what arrived.

        Asserted per RESPONSE, not per turn: a streamed answer is several publishes and
        the fields were added to the chunk path separately from the reply path
        (backlog/expressiveness.md §2.3, C2/C4), so "the turn was scored" is exactly the
        claim that would hide a chunk which was not.
        """
        if not self.chat_payloads:
            self.errors.append("no remote_chat responses to check for scored output")
            return False
        ok = True
        for p in self.chat_payloads:
            out = p.get("output") or {}
            missing = [f for f in SCORED_FIELDS if f not in out]
            got = ", ".join(f"{f}={out[f]!r}" for f in SCORED_FIELDS if f in out)
            n = p.get("chunk_num")
            where = "reply" if n is None else f"chunk {n}"
            if missing:
                ok = False
                self.errors.append(
                    f"{where} ({p.get('result')}) carried no {missing} — "
                    f"the appliance published an unscored line")
            self.log(f"🎭 {where} scored: {got or '(nothing)'}")
        return ok

    def run_scenario(self, turns):
        """Play a scripted list of turns through the real round-trip.

        `turns` = [{"say": str, "expect_contains": str?}, ...]. Each turn sends a
        remote-chat prompt and asserts a non-empty reply arrives; if
        `expect_contains` is set, the reply text must contain it (case-insensitive).
        Returns (passed:int, total:int); details go to self.errors.
        """
        passed = 0
        self.client.connect(self.host, self.port, 30)
        self.client.loop_start()
        try:
            self.client.publish(self.t_state, json.dumps(
                {"software_version": FIRMWARE, "state": "config"}))
            if not self.got_config.wait(self.timeout):
                self.errors.append("no config pushed within timeout"); return (0, len(turns))
            if (self.config_payload or {}).get("pairing_status") != "paired":
                self.errors.append("config not paired"); return (0, len(turns))
            for i, turn in enumerate(turns):
                # motor turn (SIL-only): publish a rig pose, no reply expected.
                if "motors" in turn:
                    self.client.publish(f"/devices/{self.device_id}/commands/motor",
                                        json.dumps({"motors": turn["motors"]}))
                    self.log(f"turn {i}: motors {turn['motors']} ✓")
                    passed += 1
                    time.sleep(turn.get("hold", 0.6))
                    continue
                say = turn.get("say", "")
                self._reset_turn()
                self.client.publish(self.t_event("remote-chat"), json.dumps(
                    {"event_id": str(uuid.uuid4()), "command": "prompt",
                     "backend": "router", "speech": say}))
                if not self.got_reply.wait(self.timeout):
                    self.errors.append(f"turn {i} ({say!r}): no reply"); continue
                text = self.reply_text or ((self.reply_payload or {}).get("output") or {}).get("text", "")
                if not text:
                    self.errors.append(f"turn {i} ({say!r}): empty reply"); continue
                exp = turn.get("expect_contains")
                if exp and exp.lower() not in text.lower():
                    self.errors.append(f"turn {i} ({say!r}): reply {text!r} lacks {exp!r}"); continue
                self.log(f"turn {i}: {say!r} → {text[:48]!r} ✓")
                passed += 1
            return (passed, len(turns))
        finally:
            self.client.loop_stop()
            self.client.disconnect()


def main():
    ap = argparse.ArgumentParser(description="Virtual Moxie SIL robot (protocol round-trip test).")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--device-id", default=None, help="override the d_<uuid> device id")
    ap.add_argument("--scenario", default=None, help="path to a scenario JSON (turns list)")
    ap.add_argument("--loop-seconds", type=float, default=0.0,
                    help="with --scenario: replay every N seconds (0 = once, for the demo stack)")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--expect-tts", action="store_true",
                    help="also assert a CloudTTSResponse (server voice audio) arrives")
    ap.add_argument("--expect-scored", action="store_true",
                    help="also assert every RemoteChatResponse of the turn carries the "
                         "scored output the contract specifies (mood, mood_intensity, "
                         "dialog_act, emotion, signals) — streamed chunks included")
    ap.add_argument("--reject-echo", action="store_true",
                    help="assert the reply was NOT written by the built-in echo app "
                         "(`You said: <prompt>`) — what makes a live-brain smoke a claim "
                         "about the AI seam rather than about the layers around it")
    ap.add_argument("--expect-unpaired", action="store_true",
                    help="assert the server treats us as PENDING (device allowlist): a "
                         "non-'paired' pairing_status and no child_pii; prints the config")
    ap.add_argument("--face-event", default=None,
                    help="publish vision events instead of the smoke round-trip: "
                         "'found', 'lost', or a comma-separated sequence "
                         "(e.g. 'lost,found'). Each is sent as the `speech` of a "
                         "RemoteChatRequest, which is how a real robot delivers a "
                         "subscribed perception event (docs/architecture/vision.md).")
    ap.add_argument("--face-gap", type=float, default=0.0,
                    help="with --face-event: seconds to wait between events")
    ap.add_argument("--telehealth", action="store_true",
                    help="🎭 drive the puppet/telehealth round-trip instead of the smoke "
                         "test: an operator enables Be Moxie over the supervisor's status "
                         "HTTP (--status-url), starts a session, speaks a line, interrupts "
                         "and ends — and this robot asserts the recovered wire at each step")
    ap.add_argument("--status-url", default="http://127.0.0.1:8930",
                    help="with --telehealth: the supervisor's localhost status server")
    ap.add_argument("--telehealth-line", default="Hello from the operator.",
                    help="with --telehealth: the line the operator types")
    ap.add_argument("--query", default=None,
                    help="comma-separated CloudQuery names to pull instead of the smoke "
                         "round-trip (e.g. 'schedule,mentor_behaviors')")
    ap.add_argument("--report-behavior", default=None,
                    help="with --query: a MentorBehavior JSON object to report first "
                         "(e.g. '{\"module_id\":\"DM\",\"action\":\"COMPLETED\"}')")
    args = ap.parse_args()

    vm = VirtualMoxie(args.host, args.port, args.device_id, args.timeout, not args.quiet,
                      expect_tts=args.expect_tts, expect_scored=args.expect_scored,
                      reject_echo=args.reject_echo)

    if args.expect_unpaired:
        ok = False
        try:
            ok = vm.run_unpaired()
        except Exception as e:
            vm.errors.append(f"exception: {e}")
        if ok:
            print("✅ pairing gate OK — pending: minimal config, no child_pii")
            sys.exit(0)
        print("❌ pairing gate FAILED:")
        for e in vm.errors:
            print("   -", e)
        sys.exit(1)

    if args.face_event:
        kinds = [k.strip() for k in args.face_event.split(",") if k.strip()]
        ok = False
        try:
            ok = vm.run_face_events(kinds, gap=args.face_gap)
        except Exception as e:
            vm.errors.append(f"exception: {e}")
        for rep in vm.face_replies:
            spoke = f" → {rep['text']!r}" if rep["text"] else " (silent)"
            print(f"{'✅' if rep['result'] else '❌'} {rep['kind']}: "
                  f"{rep['result']}{spoke}")
        for e in vm.errors:
            print("   -", e)
        sys.exit(0 if ok else 1)

    if args.telehealth:
        ok = False
        try:
            ok = vm.run_telehealth(args.status_url, line=args.telehealth_line)
        except Exception as e:
            vm.errors.append(f"exception: {e}")
        for rec in vm.telehealth:
            extra = f" {rec['text']!r}" if rec["text"] else ""
            print(f"   🎭 {rec['action']}{extra}")
        if ok:
            print("✅ telehealth SIL OK — enable→start→speak→interrupt→end; the robot "
                  f"spoke the operator's line and reported {vm.telehealth_state}")
            sys.exit(0)
        print("❌ telehealth SIL FAILED:")
        for e in vm.errors:
            print("   -", e)
        sys.exit(1)

    if args.query:
        names = [q.strip() for q in args.query.split(",") if q.strip()]
        report = json.loads(args.report_behavior) if args.report_behavior else None
        ok = False
        try:
            ok = vm.run_queries(names, report=report)
        except Exception as e:
            vm.errors.append(f"exception: {e}")
        for name in names:
            got = vm.query_results.get(name)
            print(f"{'✅' if got else '❌'} {name}: "
                  f"{json.dumps(got['value']) if got else 'NO ANSWER'}")
        for e in vm.errors:
            print("   -", e)
        sys.exit(0 if ok else 1)

    if args.scenario:
        with open(args.scenario) as fh:
            spec = json.load(fh)
        turns = spec.get("turns", spec) if isinstance(spec, dict) else spec
        name = spec.get("name", args.scenario) if isinstance(spec, dict) else args.scenario
        while True:                       # --loop-seconds replays for the demo stack
            vm = VirtualMoxie(args.host, args.port, args.device_id, args.timeout, not args.quiet)
            try:
                passed, total = vm.run_scenario(turns)
            except Exception as e:
                print(f"❌ scenario {name}: exception: {e}")
                if not args.loop_seconds:
                    sys.exit(1)
                passed, total = 0, len(turns)
            mark = "✅" if passed == total else "❌"
            print(f"{mark} scenario '{name}': {passed}/{total} turns OK")
            for e in vm.errors:
                print("   -", e)
            if not args.loop_seconds:
                sys.exit(0 if passed == total else 1)
            time.sleep(args.loop_seconds)

    ok = False
    try:
        ok = vm.run_smoke()
    except Exception as e:
        vm.errors.append(f"exception: {e}")
    if ok:
        # The default line is unchanged, byte for byte — `--reject-echo` only ADDS the
        # half a reader cannot otherwise see (that a model, not the stub, answered).
        print("✅ SIL round-trip OK — state→config(paired)→remote-chat→reply"
              + (" (🧠 live brain: the reply is not the echo app's)"
                 if args.reject_echo else ""))
        sys.exit(0)
    print("❌ SIL round-trip FAILED:")
    for e in vm.errors:
        print("   -", e)
    sys.exit(1)


if __name__ == "__main__":
    main()
