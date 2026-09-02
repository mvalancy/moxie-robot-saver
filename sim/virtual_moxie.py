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

Exit code 0 = the full round-trip worked. Used by the CI workflow (sim/ci/ci.yml;
install to .github/workflows/ to run it on GitHub)
and by ``sim/run_smoke.sh`` locally.

Usage:
  python3 sim/virtual_moxie.py --host 127.0.0.1 --port 1883 --timeout 15
"""
from __future__ import annotations
import argparse, json, sys, threading, time, uuid

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("virtual_moxie needs paho-mqtt:  pip install 'paho-mqtt>=2.0'")

FIRMWARE = "24.10.803"           # the analyzed build; robot reports this in /state


class VirtualMoxie:
    def __init__(self, host: str, port: int, device_id: str | None = None,
                 timeout: float = 15.0, verbose: bool = True, expect_tts: bool = False):
        self.host, self.port, self.timeout = host, port, timeout
        self.device_id = device_id or f"d_{uuid.uuid4()}"
        self.verbose = verbose
        self.expect_tts = expect_tts        # also assert a CloudTTSResponse (audio) arrives
        self.got_config = threading.Event()
        self.got_reply = threading.Event()
        self.got_tts = threading.Event()
        self.got_query = threading.Event()
        self.config_payload: dict | None = None
        self.reply_payload: dict | None = None
        self.query_results: dict = {}       # CloudQuery name -> last CloudQueryResponse
        self.spoke: dict | None = None      # last decoded CloudTTSResponse (audio playback)
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
            self.reply_payload = payload
            out = (payload.get("output") or {}).get("text", "")
            self.log(f"← remote_chat reply: {out[:60]!r}")
            self.got_reply.set()
        elif topic.endswith("/commands/tts"):
            self._play_tts(payload)
        elif topic.endswith("/commands/query_result"):
            self._on_query_result(payload)
        elif "/commands/" in topic:
            self.log(f"← {topic.split('/commands/')[-1]}: {str(payload)[:60]}")

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
                 "speech": "hello Moxie"}))
            self.log("→ events/remote-chat prompt: 'hello Moxie'")

            # 4) wait for the reply, assert it has text
            if not self.got_reply.wait(self.timeout):
                self.errors.append("no remote_chat reply within timeout")
                return False
            text = ((self.reply_payload or {}).get("output") or {}).get("text", "")
            if not text:
                self.errors.append("remote_chat reply had empty output.text")
                return False

            # 5) (optional) assert the server voice reached us as audio on /commands/tts
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
                self.got_reply.clear(); self.reply_payload = None
                self.client.publish(self.t_event("remote-chat"), json.dumps(
                    {"event_id": str(uuid.uuid4()), "command": "prompt",
                     "backend": "router", "speech": say}))
                if not self.got_reply.wait(self.timeout):
                    self.errors.append(f"turn {i} ({say!r}): no reply"); continue
                text = ((self.reply_payload or {}).get("output") or {}).get("text", "")
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
    ap.add_argument("--query", default=None,
                    help="comma-separated CloudQuery names to pull instead of the smoke "
                         "round-trip (e.g. 'schedule,mentor_behaviors')")
    ap.add_argument("--report-behavior", default=None,
                    help="with --query: a MentorBehavior JSON object to report first "
                         "(e.g. '{\"module_id\":\"DM\",\"action\":\"COMPLETED\"}')")
    args = ap.parse_args()

    vm = VirtualMoxie(args.host, args.port, args.device_id, args.timeout, not args.quiet,
                      expect_tts=args.expect_tts)

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
        print("✅ SIL round-trip OK — state→config(paired)→remote-chat→reply")
        sys.exit(0)
    print("❌ SIL round-trip FAILED:")
    for e in vm.errors:
        print("   -", e)
    sys.exit(1)


if __name__ == "__main__":
    main()
