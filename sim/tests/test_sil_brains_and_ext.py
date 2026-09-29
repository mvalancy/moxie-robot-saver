"""
Any brain per robot + sandboxed content extensions, through the real stack — a broker,
`mqtt/run.py` as its own process, and SIL robots on the wire. Both slices touch the same
turn path (the extension runs inside the content app; the brain picker chooses which app).

  1. A brain set through the real status HTTP is used by the NEXT turn (fleet, then a
     per-robot override), read back as different answers on the wire.
  2. A turn already in flight finishes with the brain it started with (a slow brain; the
     swap is posted while the robot waits).
  3. The shipped clock extension (`starter.json` G1) answers on the wire with zero model
     calls — counted, because the brain endpoint is a local counting stub.
  4. Both at once: `content` and `echo` robots on one supervisor.

No credentials, no network.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "sim"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "mqtt"))

import helpers_stack as S                                       # noqa: E402
from helpers_runtime import http_json                           # noqa: E402

#: What the stub brain says. Distinctive on purpose: every assertion below is "which of
#: three sentences came back", and a substring that could also be a fallback line would
#: make a red run look green.
BRAIN_LINE = "Stub brain speaking."


class _Brain(ThreadingHTTPServer):
    """An OpenAI-compatible `/v1/chat/completions` that counts and can stall. Threaded
    (one request parks while another thread posts) with daemon threads."""

    daemon_threads = True
    allow_reuse_address = True


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):            # keep pytest's output readable
        pass

    def do_POST(self):                    # noqa: N802 — BaseHTTPRequestHandler's spelling
        srv = self.server
        with srv.lock:
            srv.calls.append(self.path)
        delay = srv.delay
        if delay:
            srv.entered.set()             # "a turn is now in flight" — the swap races this
            time.sleep(delay)
        body = json.dumps({
            "id": "chatcmpl-stub", "object": "chat.completion", "model": "stub",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": BRAIN_LINE}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StubBrain:
    """The counting stub, as a context manager. `calls` is the whole point of it."""

    def __init__(self):
        self.httpd = _Brain(("127.0.0.1", 0), _Handler)
        self.httpd.calls = []
        self.httpd.lock = threading.Lock()
        self.httpd.delay = 0.0
        self.httpd.entered = threading.Event()
        self.port = self.httpd.server_address[1]
        self._t = None

    def __enter__(self):
        self._t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        return False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def count(self) -> int:
        with self.httpd.lock:
            return len(self.httpd.calls)

    def slow(self, seconds: float):
        self.httpd.entered.clear()
        self.httpd.delay = seconds

    def fast(self):
        self.httpd.delay = 0.0


# ----------------------------------------------------------------- the harness --
def _post(url: str, payload: dict, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:                  # a refusal is an answer too
        return json.loads(e.read() or b"{}")


class Robot:
    """A connected SIL robot driven one turn at a time: the connection is held open (the
    turn BOUNDARY is the subject) and the config round-trip is done once."""

    def __init__(self, port: int, timeout: float = 60.0):
        from virtual_moxie import VirtualMoxie
        self.vm = VirtualMoxie("127.0.0.1", port, timeout=timeout, verbose=False)
        self.vm.client.connect("127.0.0.1", port, 30)
        self.vm.client.loop_start()
        # `announce()` and not a bare publish: the config that answers a `/state` is a
        # QoS-0, non-retained message, so a robot that announces itself before its
        # SUBSCRIBE has been acknowledged is deaf to the only answer it gets — see
        # `virtual_moxie.VirtualMoxie.announce`.
        assert self.vm.announce(), self.vm.errors
        assert self.vm.got_config.wait(timeout), "no config pushed"
        assert (self.vm.config_payload or {}).get("pairing_status") == "paired", \
            self.vm.config_payload

    @property
    def device_id(self) -> str:
        return self.vm.device_id

    def ask(self, speech: str, timeout: float = 60.0) -> str:
        """One turn, start to finish; returns the reply text the robot heard."""
        self.send(speech)
        return self.await_reply(timeout)

    def send(self, speech: str) -> str:
        """Publish the prompt and return without waiting — the in-flight half."""
        self.vm._reset_turn()
        event_id = str(uuid.uuid4())
        self.vm.client.publish(self.vm.t_event("remote-chat"), json.dumps(
            {"event_id": event_id, "command": "prompt", "backend": "router",
             "speech": speech}))
        return event_id

    def await_reply(self, timeout: float = 60.0) -> str:
        assert self.vm.got_reply.wait(timeout), f"no reply within {timeout}s"
        payload = self.vm.reply_payload or {}
        return (self.vm.reply_text
                or (payload.get("output") or {}).get("text", "") or "")

    def close(self):
        try:
            self.vm.client.loop_stop()
            self.vm.client.disconnect()
        except Exception:
            pass


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    """One boot: broker, `mqtt/run.py`, and the counting stub. `MOXIE_APP=any` pins nothing
    (`brains.NO_PIN_VALUES`) so the layers can decide; a bare `docker compose up` pins, so
    it is set explicitly here.
    """
    if not S.broker_available():
        pytest.skip("no mosquitto binary and no runnable docker — cannot boot a broker")
    logs = str(tmp_path_factory.mktemp("brains-ext-sil"))
    with StubBrain() as brain:
        env = {"MOXIE_APP": "any",                  # nothing pinned: the layers decide
               "MOXIE_LLM_BASE_URL": brain.base_url,
               "MOXIE_LLM_API_KEY": "sk-stub-not-a-secret",
               "MOXIE_LLM_MODEL": "stub",
               "MOXIE_STREAMING": "off",            # one reply per turn, not a chunk queue
               "MOXIE_TTS": "off",                  # the audio path has its own smokes
               "MOXIE_BRAIN_BUDGET_S": "300",       # no filler line racing the slow turn
               "MOXIE_SKIP_DOTENV": "1",            # never find a developer's real key
               "MOXIE_CHILD_NICKNAME": "Sam"}
        with S.Stack(logs, env=env) as stack:
            yield {"stack": stack, "brain": brain,
                   "status": f"http://127.0.0.1:{stack.supervisor.status_port}"}


@pytest.fixture(scope="module")
def alice(lab):
    r = Robot(lab["stack"].port)
    yield r
    r.close()


@pytest.fixture(scope="module")
def bob(lab):
    r = Robot(lab["stack"].port)
    yield r
    r.close()


# --------------------------------------------------- 1. the appliance's own brain --
def test_the_appliance_boots_on_its_default_brain_and_says_which_layer_chose_it(lab):
    """`MOXIE_APP=any` → the default layer, and the boot line says so in words."""
    line = lab["stack"].supervisor.line_with("🧠 brain:")
    assert "brain: llm (appliance default)" in line, line
    status = http_json(lab["status"] + "/status")
    assert status.get("brain") == "llm", status
    assert status.get("brain_pin") == "", status      # `any` pins nothing


def test_the_first_turn_is_answered_by_that_brain(lab, alice):
    before = lab["brain"].count
    text = alice.ask("hello Moxie")
    assert BRAIN_LINE in text, text
    assert lab["brain"].count == before + 1, "the llm brain did not call the endpoint"


# ------------------------------------------- 2. a fleet brain, on the next turn --
def test_a_fleet_brain_set_over_http_answers_the_next_turn(lab, alice):
    """The house rule via the real status HTTP, read back as a different answer on the WIRE —
    only the next turn can say which app runs."""
    out = _post(lab["status"] + "/brain?scope=fleet", {"brain": "echo"})
    assert out.get("ok"), out
    before = lab["brain"].count
    text = alice.ask("hello again")
    assert "You said: hello again" in text, text
    assert lab["brain"].count == before, \
        "the echo brain called the model endpoint — the swap did not take"


def test_the_brain_view_attributes_that_answer_to_the_fleet_layer(lab, alice):
    view = http_json(lab["status"] + "/brain")
    assert view.get("fleet") == "echo", view
    mine = [r for r in view.get("robots") or [] if r.get("device_id") == alice.device_id]
    assert mine and mine[0]["brain"] == "echo", view
    assert mine[0]["source"] == "fleet", mine[0]


# --------------------------------------- 3. a per-robot brain on top of the fleet --
def test_a_per_robot_brain_overrides_the_fleet_one_for_that_robot_only(lab, alice, bob):
    """Alice → `content`; Bob stays on the house rule: two robots, two brains, one
    supervisor, one broker, at the same moment."""
    out = _post(lab["status"] + f"/brain?device_id={alice.device_id}",
                {"brain": "content"})
    assert out.get("ok"), out

    view = http_json(lab["status"] + "/brain")
    by_id = {r["device_id"]: r for r in view.get("robots") or []}
    assert by_id[alice.device_id]["brain"] == "content", view
    assert by_id[alice.device_id]["source"] == "robot", view
    assert by_id[bob.device_id]["brain"] == "echo", view
    assert by_id[bob.device_id]["source"] == "fleet", view

    # …and the wire agrees with the card, which is the only agreement that matters.
    assert "You said: bob is here" in bob.ask("bob is here")


# ------------------------------------ 4. the shipped extension, live, no model call --
def test_the_shipped_clock_extension_answers_on_the_wire_with_no_model_call(lab, alice):
    """G1 answers "what time is it" from a program with zero model calls, under a brain
    chosen per robot rather than by `MOXIE_APP`."""
    before = lab["brain"].count
    text = alice.ask("what time is it")
    assert text.startswith("The time is "), text
    # `AY M` is the shipped program's own TTS hint ("ay-em", not "am"). Either half is
    # accepted — which appears depends on the hour; the exact rendering is pinned in
    # `data/ext_conformance.json`. (`test_clock_dependence.py` cannot see this: the clock is
    # read by the extension inside the runtime.)
    assert text.rstrip().endswith(("AY M", "P M")), text
    assert lab["brain"].count == before, (
        f"the clock extension cost {lab['brain'].count - before} model call(s) — "
        "the point of an extension is that it costs none")


def test_a_content_turn_that_is_not_the_extension_still_reaches_the_brain(lab, alice):
    """The control for the clock test: on the same robot and brain an *unmatched* utterance
    still costs one call (a content app that failed to build would look identical)."""
    before = lab["brain"].count
    text = alice.ask("tell me about dinosaurs")
    assert BRAIN_LINE in text, text
    assert lab["brain"].count == before + 1, "the content brain never asked the model"


# ------------------------------------------- 5. the swap lands BETWEEN turns, not in --
def test_a_turn_in_flight_finishes_with_the_brain_it_started_with(lab, bob):
    """The boundary: `app_for` runs once at the top of `_handle_turn`, so a swap posted while
    a two-second brain is answering lands on the NEXT sentence — the reply is the old brain's.
    """
    assert _post(lab["status"] + f"/brain?device_id={bob.device_id}",
                 {"brain": "llm"}).get("ok")
    assert BRAIN_LINE in bob.ask("warm up"), "bob is not on the stub brain"

    lab["brain"].slow(2.0)
    try:
        bob.send("this turn is already running")
        assert lab["brain"].httpd.entered.wait(20), "the slow brain was never entered"
        # The robot is now parked inside the model call. Swap the brain underneath it.
        out = _post(lab["status"] + f"/brain?device_id={bob.device_id}",
                    {"brain": "echo"})
        assert out.get("ok"), out
        text = bob.await_reply(60)
    finally:
        lab["brain"].fast()
    assert BRAIN_LINE in text, (
        f"the in-flight turn was answered by the NEW brain ({text!r}) — the swap "
        "reached inside a turn instead of landing between two")


def test_and_the_very_next_turn_uses_the_new_one(lab, bob):
    """The other half: having not disturbed the turn in flight, the swap must be in force
    for the next one, with no restart and no reconnect — the same connection, the same
    session, a different brain."""
    before = lab["brain"].count
    text = bob.ask("and now")
    assert "You said: and now" in text, text
    assert lab["brain"].count == before, text


# ------------------------------------------------ 6. clearing and refusing --
def test_clearing_a_per_robot_brain_hands_the_robot_back_to_the_fleet(lab, alice):
    """`{"brain": null}` clears the layer rather than storing a name — the shape the
    console's "inherit" option posts. Alice goes back to the house rule."""
    out = _post(lab["status"] + f"/brain?device_id={alice.device_id}", {"brain": None})
    assert out.get("ok"), out
    view = http_json(lab["status"] + "/brain")
    mine = {r["device_id"]: r for r in view.get("robots") or []}[alice.device_id]
    assert mine["brain"] == "echo" and mine["source"] == "fleet", mine
    assert "You said: back home" in alice.ask("back home")


def test_an_unknown_brain_is_refused_by_name_and_changes_nothing(lab, alice):
    """The positive list, through the HTTP the console really posts to."""
    out = _post(lab["status"] + f"/brain?device_id={alice.device_id}", {"brain": "gpt5"})
    assert not out.get("ok"), out
    assert "gpt5" in str(out.get("error")), out
    assert "llm, content, webhook, echo" in str(out.get("error")), out
    assert "You said: still here" in alice.ask("still here")
