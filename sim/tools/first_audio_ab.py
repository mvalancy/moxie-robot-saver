#!/usr/bin/env python3
"""First-audio latency A/B across `MOXIE_EXPRESSIVE` — the real experiment, not a bench.

`backlog/expressiveness.md` §2.7 P1 criterion (f) reports the planner's cost as a
**bench measurement of the seam**: p95 0.25 ms / 0.56 ms against the floor's 0.15 / 0.29,
measured by calling `perform()` in a loop. The author qualified it in the acceptance row
itself — *"a bench measurement of the seam, not a re-run of the first-audio experiment"* —
and that qualification is the reason this file exists. The number a child feels is the one
PR #15 measured on the wire: **first words at 1.52 s, whole answer at 4.38 s**, timed from
the robot's own `events/remote-chat` publish to the `commands/remote_chat` that carries the
first sentence.

So this re-runs *that* experiment with the planner in the loop. It boots the real stack
(`helpers_stack.Stack`: a real broker, `mqtt/run.py` as its own process), connects a
protocol-faithful robot, and for each turn records — from the robot's side, which is the
only side a child's latency is defined on:

    t_words   first `commands/remote_chat` carrying non-empty output.text
    t_audio   first `commands/tts` carrying audio                     ← "first audio"
    t_done    the closing chunk (`is_completed`), i.e. the whole answer
    chunks    how many `remote_chat` publishes the turn produced

Two arms, identical in every other variable: `MOXIE_EXPRESSIVE=planner` and
`MOXIE_EXPRESSIVE=floor`. One supervisor boot per arm — the mode is read per call by
`markup.perform`, but an appliance is configured once, so an arm is a process.

**Two brains, and both are needed.**

`--brain live` points `MOXIE_APP=llm` at the configured gateway: the honest end-to-end
number, and the only one comparable to 1.52 s. It also costs one chat completion per turn
and its variance is the gateway's, not ours — on a shared endpoint the turn-to-turn spread
is hundreds of milliseconds, which is three orders of magnitude above anything the seam
can contribute. A two-turn A/B against that noise can only ever bound the planner's cost;
it cannot resolve it.

`--brain stub` stands a local OpenAI-compatible endpoint that streams a **fixed** answer
with a **fixed** inter-token delay. Same runtime, same broker, same publish path, same
`_stage` call on every chunk — with the one variable that was drowning the measurement
held still. That is where an N large enough to see 0.5 ms actually lives, and it is free.

The verdict wants both: the stub arm says what the seam costs, the live arm says whether
that cost is visible in the thing the child experiences.

`MOXIE_TTS=tone` throughout: the built-in synthesizer is local, zero-dependency and
identical in both arms, so `t_audio` measures *our* pipeline rather than a voice
provider's queue. A gateway voice would add one `/audio/speech` round trip per chunk to
both arms equally — and to the budget, per chunk.

    # free, high N, resolves the seam
    sim/tools/first_audio_ab.py --brain stub --turns 12 --mode planner
    sim/tools/first_audio_ab.py --brain stub --turns 12 --mode floor

    # one chat completion per turn — the real experiment
    sim/tools/first_audio_ab.py --brain live --turns 2 --mode planner

Prints a JSON summary on the last line so a harness can read it; everything above it is
for a human.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "sim"))
sys.path.insert(0, os.path.join(REPO, "sim", "tests"))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

import paho.mqtt.client as mqtt                                     # noqa: E402
import helpers_stack as S                                           # noqa: E402

#: What the stub brain streams. Four sentences on purpose: the segmenter splits on
#: `. ! ?` followed by real text, and a one-chunk answer would never exercise
#: `_publish_stream_chunk` — the path C4 changed and the path the planner now runs on
#: for every chunk rather than once per turn.
STUB_ANSWER = ("The moon looks different because of how the sun lights it up. "
               "Half of it is always bright, and half is always dark. "
               "We only see part of the bright half from here. "
               "That is why it seems to change shape all month.")


# --------------------------------------------------------------------------- #
# The controlled brain
# --------------------------------------------------------------------------- #
class _StubBrain(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        """The openai client keeps connections alive and drops them when it is done.

        `socketserver`'s default prints a traceback for every such reset, which buries
        the measurement in noise that means nothing. A stub brain has no errors worth
        reporting: if it ever failed to answer, the turn times out and the run says so.
        """


class _StubHandler(BaseHTTPRequestHandler):
    """`/v1/chat/completions`, streaming or not, with a fixed answer and a fixed pace.

    The pace matters: the runtime publishes a chunk when the *segmenter* says a sentence
    ended, so a brain that returns everything in one token would collapse the streaming
    path into the single-reply path and measure the wrong thing.
    """

    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):                    # noqa: N802 — BaseHTTPRequestHandler's spelling
        srv = self.server
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(body or b"{}")
        except Exception:
            req = {}
        with srv.lock:
            srv.calls += 1
        time.sleep(srv.ttft)                       # time to first token
        if req.get("stream"):
            return self._stream(srv)
        return self._whole(srv)

    def _send_head(self, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _chunk(self, raw: bytes):
        self.wfile.write(b"%x\r\n" % len(raw) + raw + b"\r\n")
        self.wfile.flush()

    def _stream(self, srv):
        self._send_head("text/event-stream")
        head = {"id": "chatcmpl-stub", "object": "chat.completion.chunk",
                "created": 0, "model": "stub"}
        for word in STUB_ANSWER.split(" "):
            frame = dict(head, choices=[{"delta": {"content": word + " "},
                                         "index": 0, "finish_reason": None}])
            self._chunk(b"data: " + json.dumps(frame).encode() + b"\n\n")
            time.sleep(srv.pace)
        tail = dict(head, choices=[{"delta": {}, "index": 0, "finish_reason": "stop"}])
        self._chunk(b"data: " + json.dumps(tail).encode() + b"\n\n")
        self._chunk(b"data: [DONE]\n\n")
        self._chunk(b"")

    def _whole(self, srv):
        payload = json.dumps({"id": "chatcmpl-stub", "object": "chat.completion",
                              "created": 0, "model": "stub",
                              "choices": [{"message": {"role": "assistant",
                                                       "content": STUB_ANSWER},
                                           "finish_reason": "stop", "index": 0}]}).encode()
        self._send_head("application/json")
        self._chunk(payload)
        self._chunk(b"")


def start_stub(ttft: float, pace: float):
    srv = _StubBrain(("127.0.0.1", 0), _StubHandler)
    srv.lock, srv.calls, srv.ttft, srv.pace = threading.Lock(), 0, ttft, pace
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"


# --------------------------------------------------------------------------- #
# The robot — a stopwatch with an MQTT client attached
# --------------------------------------------------------------------------- #
class TimedRobot:
    """The SIL handshake of `virtual_moxie.py`, instrumented.

    Deliberately not a `VirtualMoxie` subclass: that class joins chunks and wakes one
    event on the *closing* one, which is exactly the timestamp this experiment is not
    about. Here every arrival is stamped the moment paho hands it over.
    """

    FIRMWARE = "24.10.803"

    def __init__(self, host, port, device_id=None):
        self.device_id = device_id or f"d_{uuid.uuid4()}"
        self.host, self.port = host, port
        self.subscribed = threading.Event()
        self._pending_subs: set = set()
        self.paired = threading.Event()
        self.done = threading.Event()
        self.t0 = 0.0
        self.words = self.audio = self.finished = 0.0
        self.chunks = 0
        self.texts: list[str] = []
        self.scored: list[dict] = []
        self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.device_id)
        self.c.on_connect = self._on_connect
        self.c.on_subscribe = self._on_subscribe
        self.c.on_message = self._on_message

    def _on_connect(self, c, u, flags, rc, props=None):
        pending = set()
        for topic in (f"/devices/{self.device_id}/config",
                      f"/devices/{self.device_id}/commands/#"):
            pending.add(c.subscribe(topic)[1])
        self._pending_subs = pending
        self.subscribed.clear()

    def _on_subscribe(self, c, u, mid, reason_codes=None, properties=None):
        self._pending_subs.discard(mid)
        if not self._pending_subs:
            self.subscribed.set()

    def _on_message(self, c, u, msg):
        now = time.perf_counter()
        try:
            p = json.loads(msg.payload.decode("utf-8", "replace"))
        except Exception:
            return
        if msg.topic.endswith("/config"):
            if p.get("pairing_status") == "paired":
                self.paired.set()
            return
        if msg.topic.endswith("/commands/remote_chat"):
            out = p.get("output") or {}
            self.chunks += 1
            self.texts.append(out.get("text", ""))
            self.scored.append({k: out.get(k) for k in
                                ("mood", "mood_intensity", "dialog_act", "emotion",
                                 "signals", "markup") if k in out})
            if out.get("text", "").strip() and not self.words:
                self.words = now - self.t0
            cc = p.get("consistency_control") or {}
            if p.get("result") == "SUCCESS" or cc.get("is_completed"):
                self.finished = now - self.t0
                self.done.set()
            return
        if msg.topic.endswith("/commands/tts"):
            if p.get("audio") and not self.audio:
                self.audio = now - self.t0

    def connect(self, timeout=30.0):
        self.c.connect(self.host, self.port, 30)
        self.c.loop_start()
        # Announce only after the broker has ACKED the subscription that carries the
        # answer — the config is QoS 0 and not retained, so a robot that announces first
        # can lose it outright rather than late. Same rule and same reason as
        # `virtual_moxie.VirtualMoxie.announce`.
        if not self.subscribed.wait(timeout):
            raise RuntimeError("the broker never acknowledged our subscriptions")
        self.c.publish(f"/devices/{self.device_id}/state",
                       json.dumps({"software_version": self.FIRMWARE, "state": "config"}))
        if not self.paired.wait(timeout):
            raise RuntimeError("no paired config within timeout")
        return self

    def turn(self, speech: str, timeout=120.0) -> dict:
        self.words = self.audio = self.finished = 0.0
        self.chunks = 0
        self.texts, self.scored = [], []
        self.done.clear()
        event_id = str(uuid.uuid4())
        self.t0 = time.perf_counter()
        self.c.publish(f"/devices/{self.device_id}/events/remote-chat",
                       json.dumps({"event_id": event_id, "command": "prompt",
                                   "backend": "router", "speech": speech}))
        ok = self.done.wait(timeout)
        time.sleep(0.35)                  # let a trailing tts for the closing chunk land
        return {"ok": ok, "event_id": event_id, "t_words": round(self.words, 4),
                "t_audio": round(self.audio, 4), "t_done": round(self.finished, 4),
                "chunks": self.chunks, "text": " ".join(t for t in self.texts if t),
                "scored": list(self.scored)}

    def close(self):
        self.c.loop_stop()
        self.c.disconnect()


# --------------------------------------------------------------------------- #
def _pct(xs, p):
    xs = sorted(x for x in xs if x)
    if not xs:
        return 0.0
    k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
    return xs[k]


def run_arm(mode: str, args, logdir: str) -> dict:
    stub = base = None
    env = {"MOXIE_EXPRESSIVE": mode,
           # `MOXIE_TTS=tone` does NOT pin the tone engine: `config.build_synthesizer`
           # gives an auto precedence of voice-server > Piper > tone, and a
           # `MOXIE_VOICE_BASE_URL` inherited from `mqtt/.env` would silently make every
           # chunk a paid `/audio/speech` call — in both arms, so the A/B would still be
           # fair, but the budget would not survive it and `t_audio` would be measuring
           # someone else's queue. Blanked here, explicitly.
           "MOXIE_VOICE_BASE_URL": "",
           "MOXIE_VOICE_API_KEY": "",
           "MOXIE_TTS": "tone",
           "MOXIE_STT": "off",
           "MOXIE_STREAMING": "0" if args.no_stream else "1",
           "MOXIE_APP": "llm",
           "MOXIE_BRAIN_BUDGET_S": "300",     # a filler is a second voice call + a chunk
           "MOXIE_CHILD_NICKNAME": "Sam"}
    if args.brain == "stub":
        stub, base = start_stub(args.ttft, args.pace)
        env["MOXIE_LLM_BASE_URL"] = base
        env["MOXIE_LLM_API_KEY"] = "stub-not-a-secret"
        env["MOXIE_LLM_MODEL"] = "stub"
    else:
        for k in ("MOXIE_LLM_BASE_URL", "MOXIE_LLM_API_KEY", "MOXIE_LLM_MODEL"):
            if os.environ.get(k):
                env[k] = os.environ[k]
        if not env.get("MOXIE_LLM_BASE_URL"):
            raise SystemExit("--brain live needs MOXIE_LLM_BASE_URL + MOXIE_LLM_API_KEY")
    turns = []
    with S.Stack(os.path.join(logdir, mode), env=env) as stack:
        bot = TimedRobot("127.0.0.1", stack.port).connect()
        try:
            for i in range(args.warmup + args.turns):
                r = bot.turn(args.prompt, timeout=args.timeout)
                r["i"] = i - args.warmup
                warm = i < args.warmup
                print(f"  [{mode}] turn {r['i']}{' (warmup, discarded)' if warm else ''}: "
                      f"words={r['t_words']:.3f}s audio={r['t_audio']:.3f}s "
                      f"done={r['t_done']:.3f}s chunks={r['chunks']}", flush=True)
                if not warm:
                    turns.append(r)
                time.sleep(args.gap)
        finally:
            bot.close()
        log = stack.supervisor.text()
    if stub:
        stub.shutdown()
    words = [t["t_words"] for t in turns if t["t_words"]]
    audio = [t["t_audio"] for t in turns if t["t_audio"]]
    done = [t["t_done"] for t in turns if t["t_done"]]
    return {"mode": mode, "brain": args.brain, "turns": turns,
            "brain_calls": (stub.calls if stub else args.warmup + len(turns)),
            "planner_fallbacks": log.count("[markup] planner"),
            "words": {"n": len(words), "median": round(statistics.median(words), 4) if words else 0,
                      "mean": round(statistics.fmean(words), 4) if words else 0,
                      "min": round(min(words), 4) if words else 0,
                      "p95": round(_pct(words, 95), 4)},
            "audio": {"n": len(audio), "median": round(statistics.median(audio), 4) if audio else 0,
                      "mean": round(statistics.fmean(audio), 4) if audio else 0,
                      "min": round(min(audio), 4) if audio else 0,
                      "p95": round(_pct(audio, 95), 4)},
            "done": {"median": round(statistics.median(done), 4) if done else 0}}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", default="both",
                    choices=["planner", "floor", "off", "both"])
    ap.add_argument("--brain", default="stub", choices=["stub", "live"])
    ap.add_argument("--turns", type=int, default=8)
    ap.add_argument("--warmup", type=int, default=1,
                    help="turns to run and DISCARD before measuring — the first turn of "
                         "a fresh process pays for imports, the TLS handshake and the "
                         "planner's lazily-built catalogs, none of which a child's turn "
                         "pays twice")
    ap.add_argument("--prompt", default="why does the moon change shape?")
    ap.add_argument("--ttft", type=float, default=0.30, help="stub: time to first token")
    ap.add_argument("--pace", type=float, default=0.004, help="stub: seconds per token")
    ap.add_argument("--gap", type=float, default=0.4, help="seconds between turns")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--no-stream", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--logdir", default="")
    args = ap.parse_args()

    if not S.broker_available():
        raise SystemExit("no mosquitto binary and no runnable docker — cannot boot a broker")
    import tempfile
    logdir = args.logdir or tempfile.mkdtemp(prefix="first-audio-ab-")
    modes = ["planner", "floor"] if args.mode == "both" else [args.mode]
    arms = {}
    for m in modes:
        print(f"── arm: MOXIE_EXPRESSIVE={m} ({args.brain} brain, {args.turns} turns) ──",
              flush=True)
        arms[m] = run_arm(m, args, logdir)

    if "planner" in arms and "floor" in arms:
        d_w = arms["planner"]["words"]["median"] - arms["floor"]["words"]["median"]
        d_a = arms["planner"]["audio"]["median"] - arms["floor"]["audio"]["median"]
        print(f"\nΔ median first-words  planner − floor = {d_w * 1000:+.1f} ms")
        print(f"Δ median first-audio  planner − floor = {d_a * 1000:+.1f} ms")
    out = {"brain": args.brain, "turns_per_arm": args.turns,
           "prompt": args.prompt, "arms": arms}
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(out, fh, indent=2)
    print(json.dumps({m: {"words": a["words"], "audio": a["audio"],
                          "done": a["done"], "brain_calls": a["brain_calls"]}
                      for m, a in arms.items()}))


if __name__ == "__main__":
    main()
