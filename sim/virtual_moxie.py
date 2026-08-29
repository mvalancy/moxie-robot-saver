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
                 timeout: float = 15.0, verbose: bool = True):
        self.host, self.port, self.timeout = host, port, timeout
        self.device_id = device_id or f"d_{uuid.uuid4()}"
        self.verbose = verbose
        self.got_config = threading.Event()
        self.got_reply = threading.Event()
        self.config_payload: dict | None = None
        self.reply_payload: dict | None = None
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
        elif "/commands/" in topic:
            self.log(f"← {topic.split('/commands/')[-1]}: {str(payload)[:60]}")

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
    args = ap.parse_args()

    vm = VirtualMoxie(args.host, args.port, args.device_id, args.timeout, not args.quiet)

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
