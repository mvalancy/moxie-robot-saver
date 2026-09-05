"""The behavior planner on the wire — scored output, rehearsal, and all four slices at once.

P1 (#92) landed with 124 hermetic cases, 22 goldens and a 39/39 mutation check, and with
one thing none of them can do: put a **broker** between the planner and the client. Its
criterion (c) — *scored fields on 100 % of published turns, streamed included* — was
proven "through the real runtime", which means an in-process runtime with a fake MQTT
client. A `dialog_act` that is dropped by `json.dumps`, by `build_chat_response`'s
omit-when-empty rules, or by the chunk path's own argument list would pass every one of
those tests and reach no robot at all.

So this file asserts the same claim one layer out: a real mosquitto, `mqtt/run.py` as its
own process, and protocol-faithful robots reading `commands/remote_chat` off the wire.

What each section proves, and why a running stack is the only place it can be proved:

  1. **The single-reply path carries the score.** One `echo` turn, one publish, and the
     five scored fields present in `output` as the contract spells them
     (`mood`, `mood_intensity`, `dialog_act`, `emotion`, `signals` — note the **plural**:
     `_publish_chat` passes the planner's singular `signal` into `build_chat_response`'s
     `signals`, and a rename on either side of that seam is invisible in-process).
  2. **Every streamed chunk carries it too.** C2/C4's whole point: before #92 a
     `ReplyChunk` had no scored fields at all, so a streamed answer could not be scored
     even in principle. A four-sentence answer here is four publishes, and the assertion
     is over *all* of them including the closing `SUCCESS` — plus §2.2's "a chunk past
     the first plans no mood at all", which is a claim about the *markup*, on the wire.
  3. **Zero unknown ids, on the wire.** `vocab.validate_markup` runs over the markup a
     robot actually received, not over a string a test built.
  4. **The 🎬 rehearsal card, end to end.** `POST /preview` on the supervisor's real
     status HTTP *and* `POST /local/robots/{id}/preview` on the real console app, each
     driven until a robot has the message in its hands — then the captured payloads are
     played through the real `sim/web/bridge.js`, so "the SIM renders what comes back" is
     an assertion rather than a hope.
  5. **All four slices at once.** Extensions (#86), per-robot brains (#88), the planner
     (#92) and the child's voice share one turn path and had never met. One supervisor:
     `clock` on `content` answering from the shipped clock extension, `plain` on `echo`,
     `chatty` on `llm` streaming — three brains, three robots, one broker, planner on for
     all of them.
  6. **The rollback lever does not strip the score.** `MOXIE_EXPRESSIVE=floor` is the
     documented one-variable rollback; a rollback that silently emptied `dialog_act`
     would be a regression hiding inside a safety net. Its own stack, its own turn.

The brain is a **local** OpenAI-compatible stub (`sim/tools/first_audio_ab.py`, imported
rather than re-written so there is one stub in the tree): it streams a fixed four-sentence
answer at a fixed pace, which is what makes chunk 2 and chunk 3 exist to assert about.
Nothing here needs credentials and nothing reaches the network.

    .venv/bin/python -m pytest sim/tests/test_sil_performance_e2e.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for p in (REPO, os.path.join(REPO, "sim"), os.path.join(REPO, "sim", "tools"),
          os.path.join(REPO, "mqtt")):
    if p not in sys.path:
        sys.path.insert(0, p)

pytest.importorskip("paho.mqtt.client", reason="the SIL robots need paho")

import paho.mqtt.client as mqtt                                      # noqa: E402
import helpers_stack as S                                            # noqa: E402
import first_audio_ab as AB                                          # noqa: E402
from moxie_sdk import vocab                                          # noqa: E402

#: The five fields `RemoteChatOutput` carries for a scored line (ai-seam.md §2,
#: backlog/expressiveness.md §2.3 C1/C3). `signals` is plural on the wire and singular in
#: the planner's own `scored` dict; that mismatch is exactly what this file is here to
#: catch if it ever becomes a drop.
SCORED_FIELDS = ("mood", "mood_intensity", "dialog_act", "emotion", "signals")


# --------------------------------------------------------------------------- #
# A robot that keeps the receipts
# --------------------------------------------------------------------------- #
class WireRobot:
    """A SIL robot that records every `commands/*` payload verbatim, in arrival order.

    `VirtualMoxie` joins a turn's chunks into one string and wakes a single event on the
    closing one — which is the right shape for a smoke and the wrong shape here, where
    the per-chunk payload *is* the subject. So: same handshake, same topics, no joining.
    """

    FIRMWARE = "24.10.803"

    def __init__(self, port: int, timeout: float = 60.0):
        self.device_id = f"d_{uuid.uuid4()}"
        self.timeout = timeout
        self.subscribed = threading.Event()
        self._pending_subs: set = set()
        self.paired = threading.Event()
        self.closed = threading.Event()
        self.chats: list[dict] = []          # every remote_chat payload, in order
        self.others: list[tuple] = []        # (topic-suffix, payload) for everything else
        self._lock = threading.Lock()
        self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.device_id)
        self.c.on_connect = self._on_connect
        self.c.on_subscribe = self._on_subscribe
        self.c.on_message = self._on_message
        self.c.connect("127.0.0.1", port, 30)
        self.c.loop_start()
        # ANNOUNCE ONLY ONCE THE BROKER HAS ACKNOWLEDGED THE SUBSCRIPTION THAT CARRIES
        # THE ANSWER. `connect()` does not wait for CONNACK and `_on_connect` — which
        # sends our SUBSCRIBE — runs on paho's network thread, so publishing `/state`
        # from this thread on the next line used to race it. The supervisor answers a
        # `/state` with a QoS-0, NON-RETAINED `/config` (`moxie_runtime._publish`), so
        # losing that race does not delay the config, it deletes it: the 12 `no paired
        # config pushed within timeout` setup errors in CI on 2026-09-04 spent the whole
        # 60 s waiting for a message the supervisor's own log says it had already
        # published. See `virtual_moxie.VirtualMoxie.announce` for the measurement.
        if not self.subscribed.wait(timeout):
            raise RuntimeError(
                f"{self.device_id}: the broker never acknowledged our subscriptions")
        self.c.publish(f"/devices/{self.device_id}/state",
                       json.dumps({"software_version": self.FIRMWARE, "state": "config"}))
        if not self.paired.wait(timeout):
            raise RuntimeError("no paired config pushed within timeout")

    def _on_connect(self, c, u, flags, rc, props=None):
        # Mids first, `subscribed` armed second — paho dispatches both callbacks on one
        # network thread, so no SUBACK can land while this is still deciding what to wait
        # for.
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
        try:
            p = json.loads(msg.payload.decode("utf-8", "replace"))
        except Exception:
            return
        if msg.topic.endswith("/config"):
            if p.get("pairing_status") == "paired":
                self.paired.set()
            return
        if msg.topic.endswith("/commands/remote_chat"):
            with self._lock:
                self.chats.append(p)
            cc = p.get("consistency_control") or {}
            if p.get("result") == "SUCCESS" or cc.get("is_completed"):
                self.closed.set()
            return
        with self._lock:
            self.others.append((msg.topic.rsplit("/", 1)[-1], p))

    # -- driving --
    def reset(self):
        with self._lock:
            self.chats, self.others = [], []
        self.closed.clear()

    def ask(self, speech: str, timeout: float | None = None) -> list[dict]:
        """One turn; returns every `remote_chat` payload it produced, in arrival order."""
        self.reset()
        self.c.publish(f"/devices/{self.device_id}/events/remote-chat",
                       json.dumps({"event_id": str(uuid.uuid4()), "command": "prompt",
                                   "backend": "router", "speech": speech}))
        return self.wait(timeout)

    def wait(self, timeout: float | None = None) -> list[dict]:
        assert self.closed.wait(timeout or self.timeout), \
            f"{self.device_id}: no closing remote_chat within timeout"
        time.sleep(0.25)                     # let a trailing publish of the same turn land
        with self._lock:
            return list(self.chats)

    def text(self) -> str:
        with self._lock:
            return " ".join((c.get("output") or {}).get("text", "")
                            for c in self.chats).strip()

    def close(self):
        try:
            self.c.loop_stop()
            self.c.disconnect()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
def _req(url: str, payload=None, method="GET", timeout: float = 15.0):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}"), r.status
    except urllib.error.HTTPError as e:                # a refusal is an answer too
        return json.loads(e.read() or b"{}"), e.code


def _scored(payload: dict) -> dict:
    out = payload.get("output") or {}
    return {k: out[k] for k in SCORED_FIELDS if k in out}


def _base_env(**extra) -> dict:
    env = {
        "MOXIE_APP": "any",              # nothing pinned: the per-robot layer decides
        "MOXIE_STREAMING": "1",          # the path C2/C4 changed
        "MOXIE_TTS": "off",              # the audio round trip has its own smokes
        "MOXIE_STT": "off",
        # `MOXIE_TTS=off` does return None from `build_synthesizer` before the auto
        # precedence runs, so these are belt and braces — but the precedence is
        # voice-server > Piper > tone, and a base URL inherited from a developer's
        # `mqtt/.env` has cost this project real gateway calls before now.
        "MOXIE_VOICE_BASE_URL": "", "MOXIE_VOICE_API_KEY": "",
        "MOXIE_BRAIN_BUDGET_S": "300",   # no filler line racing a measured turn
        "MOXIE_SKIP_DOTENV": "1",        # never find a developer's real key
        "MOXIE_CHILD_NICKNAME": "Sam",
    }
    env.update(extra)
    return env


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    """One broker, one `mqtt/run.py`, one streaming stub brain, `MOXIE_EXPRESSIVE=planner`."""
    if not S.broker_available():
        pytest.skip("no mosquitto binary and no runnable docker — cannot boot a broker")
    logs = str(tmp_path_factory.mktemp("perf-sil"))
    stub, base = AB.start_stub(ttft=0.05, pace=0.002)
    env = _base_env(MOXIE_EXPRESSIVE="planner", MOXIE_LLM_BASE_URL=base,
                    MOXIE_LLM_API_KEY="stub-not-a-secret", MOXIE_LLM_MODEL="stub")
    try:
        with S.Stack(logs, env=env) as stack:
            yield {"stack": stack, "stub": stub, "logs": logs,
                   "status": f"http://127.0.0.1:{stack.supervisor.status_port}"}
    finally:
        stub.shutdown()


def _robot(lab, brain: str) -> WireRobot:
    try:
        r = WireRobot(lab["stack"].port)
    except RuntimeError as e:
        # The supervisor's own log is the other half of this failure and the fixture used
        # to throw it away: `no paired config pushed within timeout` next to
        # `[runtime] → pushed config to d_…` is a lost message, while the same error with
        # no push line is a supervisor that never answered. Two different bugs, one
        # message — so the log travels with the error.
        raise RuntimeError(f"{e}\n--- supervisor log ---\n"
                           f"{lab['stack'].supervisor.text()}") from None
    out, code = _req(f"{lab['status']}/brain?device_id={r.device_id}", {"brain": brain},
                     method="POST")
    assert code == 200 and out.get("ok"), (code, out)
    return r


@pytest.fixture(scope="module")
def chatty(lab):
    """The streamed path: the stub brain, four sentences, four chunks."""
    r = _robot(lab, "llm")
    yield r
    r.close()


@pytest.fixture(scope="module")
def plain(lab):
    """The single-reply path: `echo`, no model, one publish per turn."""
    r = _robot(lab, "echo")
    yield r
    r.close()


@pytest.fixture(scope="module")
def clock(lab):
    """The extension path: `content`, answering from `starter.json`'s clock program."""
    r = _robot(lab, "content")
    yield r
    r.close()


# --------------------------------------------------------------------------- #
# 1. the single-reply path
# --------------------------------------------------------------------------- #
def test_a_single_reply_turn_carries_every_scored_field_on_the_wire(plain):
    chats = plain.ask("hello Moxie")
    assert len(chats) == 1, [c.get("result") for c in chats]
    reply = chats[0]
    assert reply["result"] == "SUCCESS" and reply["command"] == "remote_chat", reply
    assert "chunk_num" not in reply, "a non-streaming reply must stay byte-shaped as before"
    got = _scored(reply)
    missing = [f for f in SCORED_FIELDS if f not in got]
    assert not missing, f"the wire dropped {missing}; output was {reply.get('output')}"
    assert isinstance(got["signals"], list) and got["signals"], got
    assert isinstance(got["mood_intensity"], int), got
    assert (reply["output"]["markup"] or "").startswith("<mark "), reply["output"]["markup"]


def test_the_score_on_the_wire_is_the_planners_and_not_a_leftover_default(plain):
    """`echo` sets no mood and no act of its own — every scored field here was minted by
    `_stage`. An `opening` on a greeting is the classifier's answer, so a wire that came
    back `statement_non_opinion` would mean the seam ran on the wrong text."""
    reply = plain.ask("hi there Moxie")[0]
    got = _scored(reply)
    assert got["dialog_act"] in vocab.DIALOG_ACTS, got
    assert got["emotion"] in vocab.EMOTION_STATES, got
    assert all(s in vocab.SIGNALS for s in got["signals"]), got
    assert got["mood"] in vocab.MOODS, got


# --------------------------------------------------------------------------- #
# 2. the streamed path — C2/C4, the gap PR #17 opened
# --------------------------------------------------------------------------- #
#: The prompt the streamed turn is driven with. Named because `LLMApp`'s own gesture
#: seed is `f"{device_id}|{speech}"` (`llm_app._turn_key`) — the floor cannot see an
#: `event_id`, which the runtime owns — so a test that wants to recompute the app's
#: markup has to know the speech, not just the reply.
STREAM_PROMPT = "why does the moon change shape?"


@pytest.fixture(scope="module")
def streamed(chatty):
    """One streamed turn, reused by every assertion below it."""
    chats = chatty.ask(STREAM_PROMPT)
    assert len(chats) >= 3, [c.get("result") for c in chats]
    return chats


def test_the_answer_really_did_stream(streamed):
    """The control. Without more than one publish, everything below is a restatement of
    the single-reply test with extra words."""
    assert [c["result"] for c in streamed[:-1]] == ["REPLY_PENDING"] * (len(streamed) - 1)
    assert streamed[-1]["result"] == "SUCCESS", streamed[-1]
    assert (streamed[-1].get("consistency_control") or {}).get("is_completed") is True
    assert [c["chunk_num"] for c in streamed] == list(range(len(streamed)))
    assert len({c["event_id"] for c in streamed}) == 1, "chunks belonged to different turns"


def test_every_streamed_chunk_carries_every_scored_field(streamed):
    """Criterion (c), through a broker. A chunk had **none** of these fields before #92."""
    for c in streamed:
        got = _scored(c)
        missing = [f for f in SCORED_FIELDS if f not in got]
        assert not missing, (f"chunk {c.get('chunk_num')} ({c['result']}) dropped "
                             f"{missing}: {c.get('output')}")


def test_only_the_first_chunk_wears_a_face(streamed):
    """§2.2's third decision, on the wire: a chunk past the first plans no mood at all, so
    a four-sentence answer holds one face instead of flipping it every sentence."""
    assert "cmd:playback-mood" in streamed[0]["output"]["markup"], streamed[0]["output"]
    for c in streamed[1:]:
        assert "cmd:playback-mood" not in c["output"]["markup"], (
            f"chunk {c['chunk_num']} re-set the face mid-answer: {c['output']['markup']}")


def test_every_chunk_still_performs_even_without_a_mood_mark(streamed):
    """The other half of the rule above: "no mood" must not mean "no performance"."""
    for c in streamed[1:]:
        assert "<mark " in c["output"]["markup"], c["output"]


# --------------------------------------------------------------------------- #
# 3. zero unknown ids — criterion (b), over the wire rather than over a corpus
# --------------------------------------------------------------------------- #
def test_no_message_a_robot_received_referenced_an_uncatalogued_id(lab, plain, chatty,
                                                                  clock, streamed):
    seen = 0
    for robot in (plain, chatty, clock):
        for c in robot.chats:
            markup = (c.get("output") or {}).get("markup", "")
            bad = vocab.validate_markup(markup)
            assert not bad, f"{robot.device_id} was sent unknown ids {bad}: {markup}"
            seen += 1
    assert seen >= 4, f"only {seen} messages inspected — this passed vacuously"


# --------------------------------------------------------------------------- #
# 4. all four slices at once (risk 4)
# --------------------------------------------------------------------------- #
def test_three_brains_three_robots_one_supervisor_all_scored(lab, plain, chatty, clock):
    """The combination nothing had ever run: the sandboxed extension (#86) under a brain
    chosen per robot (#88), beside a robot on `echo` and a robot streaming from a model,
    with the behavior planner (#92) scoring every one of them, on one broker."""
    before = lab["stub"].calls
    ext = clock.ask("what time is it")
    assert lab["stub"].calls == before, "the clock extension cost a model call"
    assert ext[-1]["output"]["text"].startswith("The time is "), ext[-1]["output"]

    echoed = plain.ask("bob is here")
    assert "You said: bob is here" in echoed[-1]["output"]["text"], echoed[-1]["output"]

    modelled = chatty.ask("tell me about the moon")
    assert lab["stub"].calls == before + 1, "the streaming robot never reached the brain"
    assert len(modelled) >= 3, [c["result"] for c in modelled]

    view, _ = _req(f"{lab['status']}/brain")
    by_id = {r["device_id"]: r for r in view.get("robots") or []}
    assert by_id[clock.device_id]["brain"] == "content", view
    assert by_id[plain.device_id]["brain"] == "echo", view
    assert by_id[chatty.device_id]["brain"] == "llm", view
    assert all(by_id[d]["source"] == "robot"
               for d in (clock.device_id, plain.device_id, chatty.device_id)), view

    for name, chats in (("extension", ext), ("echo", echoed), ("model", modelled)):
        for c in chats:
            missing = [f for f in SCORED_FIELDS if f not in (c.get("output") or {})]
            assert not missing, f"{name} chunk dropped {missing}: {c.get('output')}"


def test_the_extension_answer_is_scored_by_the_planner_not_by_the_app(clock):
    """An extension writes a sentence and nothing else — no mood, no act. So a scored
    extension line is proof the seam runs *after* whichever app answered, which is the
    property C4/C5 claim for `_publish_chat` as a whole rather than for `LLMApp`."""
    reply = clock.ask("please tell me the time")[-1]
    got = _scored(reply)
    assert got["dialog_act"] in vocab.DIALOG_ACTS, got
    assert "cmd:playback-mood" in reply["output"]["markup"], reply["output"]


# --------------------------------------------------------------------------- #
# 5. the 🎬 rehearsal card, end to end (risk 3)
# --------------------------------------------------------------------------- #
PREVIEW_LINES = [
    "I am sorry, I got that wrong.",            # apology  → Sad + a lowered gaze
    "Wow, that is amazing work!",               # appreciation → Happy + Celebrate
    "Why does the moon change shape?",          # factual_question → Curious + the tilt
    "Mm-hm, I see.",                            # backchannelling → no arm gesture at all
]


@pytest.fixture(scope="module")
def rehearsed(lab, plain):
    """Every preview line driven through the supervisor's REAL status HTTP, with the
    robot's own received messages captured beside each reply."""
    out = []
    for line in PREVIEW_LINES:
        plain.reset()
        body, code = _req(f"{lab['status']}/preview?device_id={plain.device_id}",
                          {"text": line}, method="POST")
        assert code == 200 and body.get("ok"), (code, body)
        got = plain.wait(20)
        out.append({"line": line, "reply": body, "received": got})
    return out


def test_preview_publishes_an_ordinary_remote_chat_the_robot_receives(rehearsed):
    """§2.4's guarantee that the SIM is not a special case: no new topic, no new message
    shape — the identical `remote_chat` a real turn produces."""
    for r in rehearsed:
        assert len(r["received"]) == 1, [c["result"] for c in r["received"]]
        msg = r["received"][0]
        assert msg["command"] == "remote_chat" and msg["result"] == "SUCCESS", msg
        assert msg["backend"] == "router", msg
        assert "chunk_num" not in msg and "consistency_control" not in msg, msg
        assert msg["event_id"].startswith("preview-"), msg
        assert msg["output"]["text"] == r["line"], msg["output"]
        assert msg["output"]["markup"] == r["reply"]["markup"], (
            "the markup the console was shown is not the markup the robot was sent")


def test_preview_hands_the_console_a_performance_it_can_draw(rehearsed):
    """The reply is the authoring surface: beats with mood/gesture/tree per beat, the
    line-level act, and `dropped` so a console can flag a refused id in red."""
    for r in rehearsed:
        perf = r["reply"].get("performance")
        assert isinstance(perf, dict), r["reply"]
        assert perf.get("beats"), perf
        assert perf.get("dialog_act") in vocab.DIALOG_ACTS, perf
        assert r["reply"]["dropped"] == [], r["reply"]["dropped"]
        assert r["reply"]["mode"] == "planner", r["reply"]
        for beat in perf["beats"]:
            assert beat.get("text"), beat
            assert beat.get("tree") in vocab.TREE_SET or beat.get("tree") is None, beat
            assert (beat.get("gesture") in vocab.GESTURE_SET
                    or beat.get("gesture") is None), beat


def test_preview_distinguishes_the_acts_an_author_is_rehearsing(rehearsed):
    """A rehearsal card that gave every line the same performance would be worse than
    none. Four lines, four different dialog acts, and a mood that is not always Neutral."""
    acts = {r["reply"]["scored"]["dialog_act"] for r in rehearsed}
    assert len(acts) == len(PREVIEW_LINES), acts
    moods = {r["reply"]["scored"]["mood"] for r in rehearsed}
    assert len(moods) >= 3, moods


def test_preview_calls_no_brain_and_records_no_turn(lab, plain):
    """"No brain is called, no history is written, no turn is recorded." The first is a
    number this test reads; the second and third show as the shape of what was published
    — a bare `SUCCESS` with no chunk sequence, on an id no turn ever mints."""
    before = lab["stub"].calls
    plain.reset()
    body, code = _req(f"{lab['status']}/preview?device_id={plain.device_id}",
                      {"text": "This is a rehearsal."}, method="POST")
    assert code == 200 and body.get("ok"), body
    plain.wait(20)
    assert lab["stub"].calls == before, "a rehearsal spent a model call"


def test_preview_refuses_an_unknown_robot_and_an_empty_line(lab, plain):
    body, code = _req(f"{lab['status']}/preview?device_id=d_nobody",
                      {"text": "hello"}, method="POST")
    assert code == 404 and not body.get("ok"), (code, body)
    body, code = _req(f"{lab['status']}/preview?device_id={plain.device_id}",
                      {"text": "   "}, method="POST")
    assert code == 400 and not body.get("ok"), (code, body)


def test_the_console_route_drives_the_same_rehearsal(lab, plain, tmp_path_factory):
    """`POST /local/robots/{id}/preview` on the REAL console app, proxied to the REAL
    supervisor, landing on a REAL robot. The console is the surface an author touches, and
    it is the one hop the supervisor's own tests cannot see."""
    pytest.importorskip("fastapi", reason="the console app needs fastapi")
    pytest.importorskip("httpx", reason="fastapi's TestClient needs httpx")
    sys.path.insert(0, os.path.join(REPO, "server"))
    os.environ["MOXIE_DB"] = str(tmp_path_factory.mktemp("console") / "preview-test.db")
    os.environ["MOXIE_SUPERVISOR_STATUS"] = lab["status"] + "/status"
    try:
        from fastapi.testclient import TestClient
        from moxie_server import main
    except Exception as e:                       # pynacl / segno / ... not installed
        pytest.skip(f"console app not importable: {e}")
    main.STATUS_URL = lab["status"] + "/status"  # read from the env at import time
    line = "Wow, you did it!"
    plain.reset()
    with TestClient(main.app) as c:
        r = c.post(f"/local/robots/{plain.device_id}/preview", json={"text": line})
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert body.get("ok") and body.get("published"), body
    assert body["performance"]["beats"], body
    got = plain.wait(20)
    assert len(got) == 1 and got[0]["output"]["text"] == line, got
    assert got[0]["output"]["markup"] == body["markup"], (got[0]["output"], body["markup"])


def test_the_sim_renders_what_the_rehearsal_published(rehearsed, tmp_path):
    """"…and confirm the SIM renders what comes back."

    The captured payloads — the exact bytes the robot was handed, not a re-rendered
    string — are played through the real `sim/web/bridge.js`, which is the only renderer
    of our markup anyone can execute. A planner that staged an id the SIM does not
    animate fails here instead of leaving a robot silently still."""
    if not any(os.path.exists(os.path.join(d, "node"))
               for d in os.environ.get("PATH", "").split(os.pathsep)):
        pytest.skip("node is not installed")
    cap = tmp_path / "preview-capture.json"
    cap.write_text(json.dumps({"messages": [r["received"][0] for r in rehearsed]},
                              indent=2))
    proc = subprocess.run(
        ["node", os.path.join(REPO, "sim", "test_preview_render.mjs"), str(cap)],
        capture_output=True, text=True, cwd=REPO, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "✅" in proc.stdout, proc.stdout


# --------------------------------------------------------------------------- #
# 6. the rollback lever (MOXIE_EXPRESSIVE=floor) still scores the wire
# --------------------------------------------------------------------------- #
def test_the_floor_is_a_rollback_not_a_downgrade_of_the_wire(tmp_path_factory):
    """`MOXIE_EXPRESSIVE=floor` is documented as the one-variable rollback. If rolling
    back also emptied `dialog_act`/`emotion`/`signals`, a deployment that took the safety
    net would silently stop scoring — so the lever gets its own stack and its own turn."""
    if not S.broker_available():
        pytest.skip("no broker available")
    logs = str(tmp_path_factory.mktemp("perf-sil-floor"))
    with S.Stack(logs, env=_base_env(MOXIE_EXPRESSIVE="floor", MOXIE_APP="echo")) as stack:
        bot = WireRobot(stack.port)
        try:
            reply = bot.ask("hi there Moxie")[0]
        finally:
            bot.close()
    got = _scored(reply)
    missing = [f for f in SCORED_FIELDS if f not in got]
    assert not missing, f"the floor dropped {missing} on the wire: {reply.get('output')}"
    assert not vocab.validate_markup(reply["output"]["markup"]), reply["output"]["markup"]


# --------------------------------------------------------------------------- #
# 7. WHOSE markup does the robot actually perform?  (the finding, pinned)
# --------------------------------------------------------------------------- #
#
# C6 in `backlog/expressiveness.md` §2.3 reads: *"`markup` is derived, never authored:
# `Reply.markup = render(validate(plan(text)))` — one renderer ⇒ one validator ⇒ the
# 'no unknown id' guarantee holds for every path."*
#
# On the wire that is true of the **scored fields** and only partly true of the markup,
# because `_stage`'s documented precedence is that an app's *authored* markup is spoken
# verbatim — and `LLMApp` authors markup on every reply and every chunk
# (`build_markup` → `automarkup.annotate`, the floor). So on the brain a real deployment
# runs, `MOXIE_EXPRESSIVE=planner` changes the five scored fields and **not** the
# performance: the body a child sees is still the markup floor's.
#
# That is a design gap, not a crash, and it is not this file's to decide — so it is
# **pinned** here rather than narrated. These two tests compute both candidate markups
# from the very text the robot received and say which one won. The day someone closes C6
# on the model path, `test_the_model_path_performs_the_floors_markup` goes red, which is
# exactly the notification that change should send.
def _floor_and_planner(text: str, turn_key: str, chunk_index: int):
    """`(annotate(...), perform(...))` for one line — the two candidate performances.

    `markup.mode()` reads `MOXIE_EXPRESSIVE` per call, so `perform` has to be pinned here
    — and the pin is **restored**. A test that leaves a mode behind in `os.environ`
    changes what every later test in the same process publishes, which is exactly the
    leak `test_env_hygiene_live_suites.py` exists to fence (playbook rule 20).
    """
    from moxie_sdk.automarkup import annotate
    from supervisor.markup import perform
    was = os.environ.get("MOXIE_EXPRESSIVE")
    os.environ["MOXIE_EXPRESSIVE"] = "planner"
    try:
        return (annotate(text, turn_key=turn_key, chunk_index=chunk_index),
                perform(text, turn_key=turn_key, chunk_index=chunk_index).markup)
    finally:
        if was is None:
            os.environ.pop("MOXIE_EXPRESSIVE", None)
        else:
            os.environ["MOXIE_EXPRESSIVE"] = was


def test_an_app_that_authors_no_markup_performs_the_planners(plain):
    """`echo` writes a sentence and no markup, so the seam's own `render()` is what
    reaches the robot — the case C6 describes, and it does hold here."""
    reply = plain.ask("hi there Moxie")[0]
    text, wire = reply["output"]["text"], reply["output"]["markup"]
    floor, planner = _floor_and_planner(text, reply["event_id"], 0)
    assert floor != planner, (
        "this line performs identically under both generators, so it can prove nothing "
        "about which one answered — pick another line")
    assert wire == planner, f"wire is not the planner's render:\n{wire}\n{planner}"


def test_the_model_path_performs_the_floors_markup(chatty, streamed):
    """The gap, asserted. Every chunk of a streamed model answer arrives carrying the
    **floor's** `annotate` output byte for byte, not `render(validate(plan(…)))` —
    because `LLMApp._stream_chunks` authors `ReplyChunk.markup` itself and `_stage`
    honours authored markup verbatim. The planner still scores the line (the tests
    above), so what a robot loses here is the *performance*: the act profile, the gaze
    tree and the per-clause staging never reach the wire on this path.

    If this test fails, read it as good news that needs its docs updated, not as a
    regression: it means the model path started performing the planner's own markup."""
    differed = 0
    app_key = f"{chatty.device_id}|{STREAM_PROMPT}"     # llm_app._turn_key
    for i, c in enumerate(streamed):
        text, wire = c["output"]["text"], c["output"]["markup"]
        floor, planner = _floor_and_planner(text, app_key, i)
        assert wire == floor, (
            f"chunk {i} is no longer the floor's markup — if the planner now drives the "
            f"model path, this pin and expressiveness.md §2.3 C6 both need updating\n"
            f"wire : {wire}\nfloor: {floor}")
        differed += (floor != planner)
    assert differed, ("every chunk happens to render identically under both generators, "
                      "so this run proved nothing — the pin passed vacuously")
