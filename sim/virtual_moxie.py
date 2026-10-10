#!/usr/bin/env python3
"""
🤖 Virtual Moxie — a software-in-the-loop (SIL) robot.

Speaks the MQTT protocol recovered from firmware OTA v24.10.803
(docs/reverse-engineering/cloud-protocol.md), so the server can be tested with no
hardware. It decodes the wire itself and never imports the server SDK it tests.

The protocol round-trip a real Moxie performs:
  1. Connect to the broker with client_id ``d_<uuid>`` (the robot's device id form).
  2. Subscribe to ``/devices/{id}/config`` and ``/devices/{id}/commands/#``.
  3. Publish ``/devices/{id}/state`` {software_version: 24.10.803} — this is what
     makes the supervisor register the robot and push its config.
  4. Assert the pushed config has ``pairing_status == "paired"``.
  5. Publish a ``/devices/{id}/events/remote-chat`` prompt ("hello").
  6. Assert a ``/devices/{id}/commands/remote_chat`` reply with ``output.text`` arrives.
     One turn may be several chunked responses (see ``_on_chat_reply``).
  7. With ``--notify``: report what it said, as a real Moxie does after each utterance
     (a ``command: "notify"`` request, mqtt-and-conversation.md §4.2), once per turn or
     once per chunk (``--notify chunk``).

Exit code 0 = the round-trip worked. Used by CI and ``sim/run_smoke.sh``.

Usage:
  python3 sim/virtual_moxie.py --host 127.0.0.1 --port 1883 --timeout 15
  python3 sim/virtual_moxie.py --notify             # ...and report what it said
  python3 sim/virtual_moxie.py --expect-unpaired    # assert the device allowlist gates us
"""
from __future__ import annotations
import argparse, json, re, sys, threading, time, uuid

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("virtual_moxie needs paho-mqtt:  pip install 'paho-mqtt>=2.0'")

FIRMWARE = "24.10.803"           # the analyzed build; robot reports this in /state

#: The scored half of `RemoteChatOutput` (ai-seam.md §2); `--expect-scored` asserts these
#: on every response of a turn. `signals` is plural on the wire (`repeated`).
SCORED_FIELDS = ("mood", "mood_intensity", "dialog_act", "emotion", "signals")

#: The smoke's prompt; `--reject-echo` reconstructs the echo app's answer to it.
SMOKE_PROMPT = "hello Moxie"

#: What `moxie_sdk/apps/echo_app.py` answers with (the no-brain app).
ECHO_TEMPLATE = "You said: {speech}"


def is_echo_reply(text: str, prompt: str = SMOKE_PROMPT) -> bool:
    """Whether `text` is the echo app's verbatim answer to `prompt` (markup stripped,
    since an echoed line wearing `<mark>` tags is still an echo)."""
    bare = re.sub(r"<[^>]*>", "", text or "").strip()
    return bare == ECHO_TEMPLATE.format(speech=prompt).strip()


#: 🎬 The action verbs this client implements: the recovered `ActionID` names our server
#: emits (`moxie_sdk.types.ACTION_IDS`) plus the two older spellings it used to send
#: (`exit`, `enable_qr`), kept for older doubles. Same list as
#: `sim/web/bridge/actions.js::ACTION_KINDS`. A literal on purpose (no SDK import);
#: `test_sim_client_parity.py` pins the three lists together.
ACTION_KINDS = ("launch", "exit", "exit_module", "sleep", "enable_qr", "execute")

#: `RemoteChatResponse.ResultCode.REPLY_PENDING` (RemoteChat.proto:317): `result` is a
#: `uint32` on the wire (:320), so the server sends the number; the name is still
#: understood for older doubles.
REPLY_PENDING = 9

#: How this robot reports what it said (`--notify`): one notify per turn with every chunk
#: it spoke, or one per spoken chunk. Which one a real Moxie uses is not captured yet
#: (mqtt-and-conversation.md §4.2), so the cloud must survive both.
NOTIFY_CADENCES = ("event", "chunk")


class VirtualMoxie:
    def __init__(self, host: str, port: int, device_id: str | None = None,
                 timeout: float = 15.0, verbose: bool = True, expect_tts: bool = False,
                 expect_scored: bool = False, reject_echo: bool = False,
                 status_url: str | None = None, notify: str | None = None):
        self.host, self.port, self.timeout = host, port, timeout
        self.device_id = device_id or f"d_{uuid.uuid4()}"
        self.verbose = verbose
        self.expect_tts = expect_tts        # also assert a CloudTTSResponse (audio) arrives
        self.expect_scored = expect_scored  # ...and that every response carries its score
        self.reject_echo = reject_echo      # ...and that a real brain, not `echo`, wrote it
        if notify not in (None, *NOTIFY_CADENCES):
            raise ValueError(f"notify must be one of {NOTIFY_CADENCES} or None, not {notify!r}")
        #: Report what it said (`_notify_spoke`): None, "event" or "chunk".
        self.notify = notify
        #: The prompts this robot sent, by event_id: what each notify echoes back.
        self._asked: dict[str, dict] = {}
        #: Every notify this robot sent, in order.
        self.notified: list = []
        #: The supervisor's localhost status server; used only by `_why_no_config`.
        self.status_url = status_url
        #: Set when the broker has ACKed every subscription (see `announce()`).
        self.subscribed = threading.Event()
        self._pending_subs: set = set()
        self.got_config = threading.Event()
        self.got_reply = threading.Event()
        self.got_tts = threading.Event()
        self.got_query = threading.Event()
        self.config_payload: dict | None = None
        self.reply_payload: dict | None = None   # the FINAL RemoteChatResponse of a turn
        self.reply_text: str = ""                # every chunk of that turn, in order
        self._chunks: dict[str, dict[int, str]] = {}   # event_id -> {chunk_num: text}
        #: Every RemoteChatResponse of the current turn, in arrival order.
        self.chat_payloads: list = []
        self.query_results: dict = {}       # CloudQuery name -> last CloudQueryResponse
        self.module_list: list | None = None   # last `query_data.modules` (module query)
        self.spoke: dict | None = None      # last decoded CloudTTSResponse (audio playback)
        self.face_replies: list = []        # what the server answered each vision event
        # 🎭 telehealth: commands received, and the state we reported.
        self.got_telehealth = threading.Event()
        self.telehealth: list = []
        self.telehealth_state: str = ""
        # 🎬 What `response_actions` did to this robot, shaped like bridge/actions.js's
        # `actionStats()`. Client lifetime, not per turn (as on the browser SIM).
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
        self.client.on_subscribe = self._on_subscribe
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

    # -- the handshake: SUBSCRIBE, then announce (never the other way round) --
    # `connect()` does not wait for CONNACK and our SUBSCRIBE is only sent from
    # `_on_connect`, so publishing `/state` straight away races it. The supervisor answers
    # with `/config` at QoS 0, not retained: losing the race deletes the config rather
    # than delaying it, and no timeout can fix that. (The supervisor's 1 s settle timer
    # hides small delays; `sim/tests/test_sil_handshake.py` tests the ordering directly.)
    SUBACK_TIMEOUT_S = 30.0

    def _why_no_config(self) -> str:
        """Say WHICH it was when the config wait expires: starved, or wedged.

        An expired wait means "we stopped waiting", not "the appliance is broken" (a loaded
        host starves the supervisor too). So ask the status server — a different transport
        from the MQTT that went quiet — with a 3 s cap. Never raises.
        """
        import urllib.error
        import urllib.request

        base = (self.status_url or "").rstrip("/")
        if not base:
            return ("supervisor liveness NOT CHECKED (no --status-url was passed), so this "
                    "line says nothing about starved vs wedged")
        t0 = time.time()
        try:
            with urllib.request.urlopen(f"{base}/status", timeout=3) as r:
                ms = int((time.time() - t0) * 1000)
                r.read(1)
                return (f"but the supervisor IS ALIVE (GET /status -> {r.status} in {ms} ms): "
                        "reachable, and it did not answer the /state - a lost message or a "
                        "starved process, NOT a wedged appliance")
        except Exception as exc:                    # noqa: BLE001 - any failure is the answer
            ms = int((time.time() - t0) * 1000)
            return (f"and the supervisor did NOT answer /status either "
                    f"({type(exc).__name__} after {ms} ms) - wedged, gone, or starved past 3 s")

    def announce(self, state: str = "config") -> bool:
        """Publish `/state`, but only once the broker has SUBACKed our subscriptions, so
        the config push it triggers has somewhere to land. False (+ an error) if never."""
        if not self.subscribed.wait(self.SUBACK_TIMEOUT_S):
            self.errors.append(
                f"the broker did not acknowledge our subscriptions within "
                f"{self.SUBACK_TIMEOUT_S:.0f}s — announcing now would make this robot "
                f"deaf to its own config push")
            return False
        self.client.publish(self.t_state, json.dumps(
            {"software_version": FIRMWARE, "state": state}))
        self.log(f"→ state (software_version={FIRMWARE})")
        return True

    def _on_connect(self, c, u, flags, rc, props=None):
        self.log(f"connected to broker rc={rc} as {self.device_id}")
        # Safe: both callbacks run on paho's one network thread, so no SUBACK is handled
        # before the pending mids are recorded.
        pending = set()
        for topic in (self.t_config, self.t_commands):
            pending.add(c.subscribe(topic)[1])
        self._pending_subs = pending
        self.subscribed.clear()             # a reconnect re-subscribes: re-arm, not latch

    def _on_subscribe(self, c, u, mid, reason_codes=None, properties=None):
        """A SUBACK. When the last one lands the robot is safe to announce itself."""
        self._pending_subs.discard(mid)
        if not self._pending_subs:
            self.log("subscriptions acknowledged by the broker")
            self.subscribed.set()

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
        """Accumulate one turn's answer, which may arrive as several responses.

        ``result=REPLY_PENDING`` means more to come, ``chunk_num`` orders them, and a
        terminal ``result`` or ``consistency_control.is_completed`` closes the turn
        (mqtt-and-conversation.md §4.5). Chunks are joined in order; the waiter wakes on the
        closing one. ``reply_payload`` is the final response, ``reply_text`` the whole line.
        """
        event_id = payload.get("event_id") or ""
        chunk_num = payload.get("chunk_num")
        text = (payload.get("output") or {}).get("text", "")
        self.chat_payloads.append(payload)
        # Actions are applied per response (any chunk may carry one) and before
        # `got_reply` is set, so a woken waiter sees settled action state.
        self._on_actions(payload)
        parts = self._chunks.setdefault(event_id, {})
        parts[int(chunk_num) if chunk_num is not None else 0] = text
        result = payload.get("result")
        completed = bool((payload.get("consistency_control") or {}).get("is_completed"))
        pending = result in (REPLY_PENDING, "REPLY_PENDING") and not completed
        if isinstance(payload.get("query_data"), dict):
            # A module-query answer: `RemoteDataBlock.modules` (RemoteChat.proto:296-300).
            self.module_list = payload["query_data"].get("modules")
        if pending:
            self.log(f"← remote_chat chunk {chunk_num}: {text[:60]!r} (more to come)")
            self._notify_spoke(event_id, text, closing=False)
            return
        self.reply_payload = payload
        self.reply_text = " ".join(parts[k] for k in sorted(parts) if parts[k]).strip()
        self.log(f"← remote_chat reply ({len(parts)} chunk(s)): {self.reply_text[:60]!r}")
        # Before the waiter wakes, so whatever it sends next follows the report.
        self._notify_spoke(event_id, text, closing=True)
        self.got_reply.set()

    # -- notify: the robot reports what it said (mqtt-and-conversation.md §4.2) --
    def send_prompt(self, speech: str, *, event_id: str | None = None,
                    module_id: str = "", content_id: str = "") -> str:
        """Publish one `remote-chat` prompt and remember it, so the notify for its answer
        can echo the child's line back in `extra_lines`."""
        event_id = event_id or str(uuid.uuid4())
        payload = {"event_id": event_id, "command": "prompt", "backend": "router",
                   "speech": speech}
        if module_id:
            payload["module_id"] = module_id
        if content_id:
            payload["content_id"] = content_id
        self._asked[event_id] = {"speech": speech, "module_id": module_id,
                                 "content_id": content_id, "notified": False}
        self.client.publish(self.t_event("remote-chat"), json.dumps(payload))
        return event_id

    def _notify_spoke(self, event_id, chunk_text, closing):
        """With `--notify`, report what was just said for one of OUR prompts: the contract's
        notify (`speech` = what Moxie said, `extra_lines` = the child's line as an `input`
        context), once per turn with every chunk joined ("event") or once per spoken chunk
        ("chunk", the child's line on the first only). A module-query answer or a vision
        reply answers no prompt of ours and is never reported."""
        asked = self._asked.get(event_id)
        if not self.notify or asked is None:
            return
        if self.notify == "chunk":
            spoken = chunk_text
        elif closing:
            spoken = self.reply_text
        else:
            return
        if not (spoken or "").strip():
            return
        payload = {"event_id": str(uuid.uuid4()), "command": "notify", "backend": "router",
                   "speech": spoken, "software_version": FIRMWARE,
                   "module_name": "virtual-moxie"}
        if not asked["notified"] and asked["speech"]:
            payload["extra_lines"] = [{"context_type": "input", "text": asked["speech"]}]
        asked["notified"] = True
        for key in ("module_id", "content_id"):
            if asked[key]:
                payload[key] = asked[key]
        self.client.publish(self.t_event("remote-chat"), json.dumps(payload))
        self.notified.append(payload)
        self.log(f"→ events/remote-chat notify: {spoken[:60]!r}")

    def settle_notify(self) -> bool:
        """The barrier after a notify: a module query sent behind it is answered only once
        the cloud has read the notify (one MQTT loop, messages in order), so whoever
        reads the cloud's transcript next reads it settled. False (+ an error) if no
        notify went out or the query is never answered."""
        if not self.notified:
            self.errors.append("--notify: the turn closed but this robot sent no notify")
            return False
        self.module_list = None
        self.send_module_query()
        if not self.got_reply.wait(self.timeout) or self.module_list is None:
            self.errors.append("--notify: the module query sent after the notify was never "
                               "answered, so the notify cannot be shown to have been read")
            return False
        self.log(f"notify read by the cloud ({len(self.notified)} sent)")
        return True

    def _reset_turn(self):
        """Forget the previous turn's chunks before sending the next prompt."""
        self.got_reply.clear()
        self.reply_payload = None
        self.reply_text = ""
        self._chunks.clear()
        self.chat_payloads = []
        # Only the edge is per turn; `self.actions` is client state.
        self.got_action.clear()

    # -- 🎬 response_actions: the brain drives this robot, not just its mouth --
    # `RemoteChatAction`s (remote-chat-protocol.md) launch/exit modules, sleep, run a named
    # function, and subscribe to perception events. This client RECORDS them, state for
    # state like `bridge/actions.js::applyAction`, and runs nothing: no module engine, no
    # `execute_returns[]` (it would have to invent a return value), no sleep/wake, no camera.

    def _on_actions(self, payload: dict):
        """Consume one response's `response_actions` (mirror of `bridge/actions.js::handleActions`).

        The legacy singular `response_action` mirrors `[0]`, so it is read only when the
        plural is absent. An entry with no `action` is a subscription-only entry. Never
        raises: an unknown future verb must not break a turn.
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
        robot to push it perception events (remote-chat-protocol.md:81-84)."""
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
        """`action_args` (`repeated ActionArgsEntry{key, value}`) as a dict; None when absent
        or unreadable so the caller falls through to its next spelling."""
        if not isinstance(entries, list):
            return None
        pairs = [(e.get("key"), e.get("value")) for e in entries if isinstance(e, dict)]
        return {str(k): v for k, v in pairs if k is not None} or None

    def _apply_action(self, entry: dict) -> bool:
        """Record one `RemoteChatAction`. Returns False for a verb we do not implement.

        Function name: `function_id` (RemoteChat.proto field 7) first, then the older
        `function`. Args: `function_args`, then `action_args`, then `args` — each tested for
        absence, not falsiness. Records only; nothing is run.
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
            self.actions["module_id"] = module_id
            self.actions["content_id"] = content_id
            self.actions["asleep"] = False
            self.actions["launches"] += 1
            self.log(f"🎬 launch {module_id}" + (f":{content_id}" if content_id else ""))
        elif kind in ("exit", "exit_module"):
            self.actions["module_id"] = ""
            self.actions["content_id"] = ""
            self.actions["exits"] += 1
            self.log(f"🎬 {kind}")
        elif kind == "sleep":
            self.actions["asleep"] = True
            self.log("🎬 sleep")
        elif kind == "enable_qr":
            self.actions["qr_enabled"] = True
            self.log("🎬 QR scanning on")
        elif kind == "execute":
            self.log(f"🎬 execute {function or '(unnamed)'}")
            # `execute eb_enable_qr ["true"]` is how the server arms the QR reader since
            # 2026-10 (qr-launch-cards.md §P0-a): recorded like the older `enable_qr`,
            # mirror of `bridge/actions.js`. Still nothing is run.
            if (function == "eb_enable_qr" and isinstance(args, list) and args
                    and str(args[0]).lower() == "true"):
                self.actions["qr_enabled"] = True
                self.log("🎬 QR scanning on")
        self.actions["last"] = kind
        self.actions["applied"].append({"action": kind, "module_id": module_id,
                                        "content_id": content_id, "function": function,
                                        "args": args if args is not None else []})
        if len(self.actions["applied"]) > 40:       # bounded, like the browser SIM's
            self.actions["applied"].pop(0)
        self.got_action.set()
        return True

    def action_stats(self) -> dict:
        """What the cloud's actions did to this robot — same keys as bridge/actions.js's
        `actionStats()` (parity: `test_sim_client_parity.py`)."""
        a = self.actions
        return {"applied": [dict(x) for x in a["applied"]], "unknown": a["unknown"],
                "module_id": a["module_id"], "content_id": a["content_id"],
                "launches": a["launches"], "exits": a["exits"], "asleep": a["asleep"],
                "qr_enabled": a["qr_enabled"], "subscribed": list(a["subscribed"]),
                "last": a["last"]}

    def _play_tts(self, payload):
        """Consume a CloudTTSResponse: decode the base64 AudioBuffer + marks and record that
        Moxie spoke, so tests can assert the voice arrived."""
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
    # The response field each answer is keyed under (recovered Cloud.proto:310-352).
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
        """Publish a CloudQueryRequest (Cloud.proto:292-305) on the activity-log topic, as
        the robot pulls its schedule/history at session start."""
        request_id = str(uuid.uuid4())
        self.client.publish(self.t_event("client-service-activity-log"), json.dumps(
            {"timestamp": int(time.time() * 1000), "subtopic": "query", "query": query,
             "request_id": request_id, "auid": self.device_id,
             "software_version": FIRMWARE, "module_name": "virtual-moxie"}))
        self.log(f"→ events/client-service-activity-log query={query!r} id={request_id}")
        return request_id

    def send_module_query(self) -> str:
        """Ask which modules the cloud serves: a RemoteChatRequest with `backend: "data"`
        and `query: RemoteDataQuery{query: modules}` (RemoteChat.proto:41-51, :79) on the
        remote-chat topic. The answer lands in `module_list` (via `_on_chat_reply`)."""
        self._reset_turn()
        event_id = str(uuid.uuid4())
        self.client.publish(self.t_event("remote-chat"), json.dumps(
            {"timestamp": int(time.time() * 1000), "event_id": event_id,
             "backend": "data", "query": {"query": "modules"},
             "software_version": FIRMWARE, "module_name": "virtual-moxie"}))
        self.log(f"→ events/remote-chat module query id={event_id}")
        return event_id

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
            if not self.announce():
                return False
            # A config push is optional here: a known robot may not get one.
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

    # -- vision (docs/architecture/vision.md) --
    # On-device vision emits semantic events only; a subscribed event reaches the brain as
    # the `speech` of an ordinary RemoteChatRequest, so it is published like an utterance.
    FACE_EVENTS = {"found": "eb-found-face", "lost": "eb-lost-target"}

    #: Marker events carrying a value (QR string, ArUco id, book cover) and their
    #: `input_vars` key (vision.md:73-74). A deliberate copy of `presence.VALUE_KEYS`: a
    #: robot that borrowed the server's constants could not detect the server changing them.
    EVENT_VALUE_KEYS = {"eb-qr-event": "$eb_qr_value",
                        "eb-dr-event": "$eb_dr_value",
                        "eb-br-event": "$eb_br_value"}

    @classmethod
    def value_vars(cls, name: str, value) -> dict | None:
        """`input_vars` carrying `value` for a marker event — or None (never `{}`, which no
        real robot sends)."""
        key = cls.EVENT_VALUE_KEYS.get(name)
        if not key or value in (None, ""):
            return None
        return {key: str(value)}

    def send_face_event(self, kind: str, input_vars: dict | None = None,
                        value=None) -> str:
        """Publish one vision event. `kind` is `found`/`lost` or a raw `eb-*` name.

        `value` is what the camera read (routed by `value_vars`); an explicit `input_vars`
        wins.
        """
        name = self.FACE_EVENTS.get(kind, kind)
        event_id = str(uuid.uuid4())
        payload = {"event_id": event_id, "command": "prompt", "backend": "router",
                   "speech": name, "module_name": "virtual-moxie"}
        if input_vars is None:
            input_vars = self.value_vars(name, value)
        if input_vars:
            payload["input_vars"] = input_vars
        self.client.publish(self.t_event("remote-chat"), json.dumps(payload))
        self.log(f"→ events/remote-chat vision event: {name!r}"
                 + (f" carrying {value!r}" if value not in (None, "") else ""))
        return event_id

    def run_face_events(self, kinds, gap: float = 0.0, value=None) -> bool:
        """Announce, then play vision events, asserting the server answers each.

        `NOREPLY_ACK` (silent) and `SUCCESS` (spoken) both count as answered. `value` is the
        marker payload every event carries (`--face-value`). Each `face_replies` row records
        the result, text and the actions this robot applied during that turn.
        """
        self.face_replies = []
        self.client.connect(self.host, self.port, 30)
        self.client.loop_start()
        try:
            if not self.announce():
                return False
            if not self.got_config.wait(min(5.0, self.timeout)):
                self.log("(no config push — already-known robot; continuing)")
            for i, kind in enumerate(kinds):
                if i and gap:
                    time.sleep(gap)
                self._reset_turn()
                before = len(self.actions["applied"])
                self.send_face_event(kind, value=value)
                if not self.got_reply.wait(self.timeout):
                    self.errors.append(f"{kind!r}: no response to the vision event")
                    continue
                resp = self.reply_payload or {}
                text = (resp.get("output") or {}).get("text", "")
                applied = [dict(a) for a in self.actions["applied"][before:]]
                self.face_replies.append({"kind": kind, "result": resp.get("result"),
                                          "text": text, "event_id": resp.get("event_id"),
                                          "actions": applied})
                self.log(f"   {kind}: result={resp.get('result')} text={text[:60]!r}"
                         + (f" actions={applied}" if applied else ""))
            return not self.errors
        finally:
            self.client.loop_stop()
            self.client.disconnect()

    # -- the pairing gate: what a NOT-permitted robot is served --
    def run_unpaired(self) -> bool:
        """Announce ourselves and assert we are treated as pending by the (closed-by-default)
        permit list: not `"paired"` and no `child_pii`. Prints the config received."""
        self.client.connect(self.host, self.port, 30)
        self.client.loop_start()
        try:
            if not self.announce():
                return False
            if not self.got_config.wait(self.timeout):
                self.errors.append(f"no config pushed within {self.timeout:g}s {self._why_no_config()}")
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

    # -- 🎭 telehealth / "Be Moxie" (protocol/telehealth.md) --
    # The cloud sends `TelehealthRobotCommand` on `commands/telehealth`; the robot reports
    # `RobotState` on the activity log's `telehealth` subtopic. The operator is driven over
    # the supervisor's status HTTP, as the console does.

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
        """End-to-end puppet check over the supervisor's status server (the endpoint the
        console proxies): enable → start → speak → GET → interrupt → end.

        Asserts: `/config` flips `moxie_mode` to TELEHEALTH; `PLAY_OUTPUT` carries text and
        markup; `INTERRUPT` carries no `output`; the supervisor's view shows our state and
        the operator's line.
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
            if not self.announce():
                return False
            if not self.got_config.wait(self.timeout):
                self.errors.append(f"no config pushed within {self.timeout:g}s {self._why_no_config()}")
                return False
            self.report_telehealth_state("READY")

            # Cleared BEFORE the call: the config is re-pushed before the HTTP reply returns.
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
            # 1) announce presence via /state (registers us + triggers config push) —
            #    but only once the broker has acked our SUBSCRIBEs; see `announce()`.
            if not self.announce():
                return False

            # 2) wait for config, assert paired
            if not self.got_config.wait(self.timeout):
                self.errors.append(f"no config pushed within {self.timeout:g}s {self._why_no_config()}")
                return False
            ps = (self.config_payload or {}).get("pairing_status")
            if ps != "paired":
                self.errors.append(f"config pairing_status={ps!r}, expected 'paired'")
                return False

            # 3) send a remote-chat prompt
            self.send_prompt(SMOKE_PROMPT)
            self.log(f"→ events/remote-chat prompt: {SMOKE_PROMPT!r}")

            # 4) wait for the reply, assert it has text
            if not self.got_reply.wait(self.timeout):
                self.errors.append("no remote_chat reply within timeout")
                return False
            text = self.reply_text or ((self.reply_payload or {}).get("output") or {}).get("text", "")
            if not text:
                self.errors.append("remote_chat reply had empty output.text")
                return False

            # 4b) (optional) assert a real brain, not the echo app, wrote it (--live-brain).
            if self.reject_echo:
                if is_echo_reply(text, SMOKE_PROMPT):
                    self.errors.append(
                        f"the reply is the echo app's own answer ({text!r}) — this run "
                        f"was supposed to be driven by a real brain, so the supervisor "
                        f"is still on MOXIE_APP=echo (or fell back to it)")
                    return False
                self.log(f"🧠 live brain reply: {text!r}")

            # 5) (optional) assert the appliance scored every response.
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

            # 7) (--notify) the report went out with the reply (`_notify_spoke`); last, so
            #    nothing above waits behind its barrier.
            if self.notify and not self.settle_notify():
                return False
            return True
        finally:
            self.client.loop_stop()
            self.client.disconnect()

    def check_scored(self) -> bool:
        """Every response of the last turn carries every scored field (per response, so an
        unscored streamed chunk cannot hide). Logs what arrived."""
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
            if not self.announce():
                return (0, len(turns))
            if not self.got_config.wait(self.timeout):
                self.errors.append(f"no config pushed within {self.timeout:g}s {self._why_no_config()}"); return (0, len(turns))
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
                self.send_prompt(say)
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
    ap.add_argument("--notify", nargs="?", const="event", default=None,
                    choices=NOTIFY_CADENCES,
                    help="after each answer to one of its prompts, report what it said "
                         "the way a real Moxie does: a remote-chat `command: notify` with "
                         "the spoken text in `speech` and the child's line as an `input` "
                         "extra_line (mqtt-and-conversation.md §4.2). 'event' (the default) "
                         "sends one per turn, 'chunk' one per spoken chunk. The smoke then "
                         "waits until the cloud has read it (a module query sent behind it "
                         "is answered).")
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
    ap.add_argument("--face-value", default=None,
                    help="with --face-event: the payload a MARKER event carries, sent as "
                         "input_vars['$eb_qr_value'] / ['$eb_dr_value'] / ['$eb_br_value'] "
                         "for eb-qr-event / eb-dr-event / eb-br-event. A 🎴 launch card is "
                         "a QR value: --face-event eb-qr-event --face-value 'GO<launch:DM>'")
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
                      reject_echo=args.reject_echo,
                      status_url=args.status_url, notify=args.notify)

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
            ok = vm.run_face_events(kinds, gap=args.face_gap, value=args.face_value)
        except Exception as e:
            vm.errors.append(f"exception: {e}")
        for rep in vm.face_replies:
            spoke = f" → {rep['text']!r}" if rep["text"] else " (silent)"
            did = "".join(f" 🎬 {a['action']}"
                          + (f" {a['module_id']}" if a["module_id"] else "")
                          for a in rep.get("actions") or [])
            print(f"{'✅' if rep['result'] else '❌'} {rep['kind']}: "
                  f"{rep['result']}{spoke}{did}")
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
            vm = VirtualMoxie(args.host, args.port, args.device_id, args.timeout, not args.quiet,
                              status_url=args.status_url, notify=args.notify)
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
        print("✅ SIL round-trip OK — state→config(paired)→remote-chat→reply"
              + ("→notify" if args.notify else "")
              + (" (🧠 live brain: the reply is not the echo app's)"
                 if args.reject_echo else ""))
        sys.exit(0)
    print("❌ SIL round-trip FAILED:")
    for e in vm.errors:
        print("   -", e)
    sys.exit(1)


if __name__ == "__main__":
    main()
