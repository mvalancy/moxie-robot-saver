"""💬 Try it — talk to any content module, or any brain this appliance offers, with no robot
(`moxie_runtime/tryit.py`, console `GET/POST /local/tryit`, `server/static/js/tryit.js`).

The claim under test is "the SAME turn a robot gets, published nowhere": the same app and
brain selection (`app_named`, the registry, the `MOXIE_APP` pin), the same safety
classifier and redirect, the same stager — and no MQTT publish, no transcript, no memory,
no safety journal. Real `MoxieRuntime` + its real status server + the real console app
in-process; the only stubs are the brains' endpoints (an OpenAI-shaped client that records
every request). No network, no sleeps on the happy path.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

pytest.importorskip("paho.mqtt.client", reason="the runtime's transport")

from helpers_console import console_app, console_js, set_status_url       # noqa: E402
from helpers_runtime import (REPO, drive_turn, fresh_pool, http_call,     # noqa: E402
                             make_runtime, status_server)
from moxie_sdk import brains, chat as chat_seam                           # noqa: E402
from moxie_sdk import safety as safety_seam                               # noqa: E402
from moxie_sdk.apps import EchoApp                                        # noqa: E402
from moxie_sdk.apps.llm_app import LLMApp                                 # noqa: E402
from moxie_sdk.content import ContentApp                                  # noqa: E402
from moxie_sdk.content import ext as E                                    # noqa: E402
from moxie_sdk.content.module import load_modules                         # noqa: E402
from moxie_sdk.memory_store import MemoryStore                            # noqa: E402
from moxie_sdk.store import JsonStore                                     # noqa: E402

DEVICE = "d_try"
DEAD = "http://127.0.0.1:1/status"

#: Two conversations (one with a memory namespace) and a command whose extension both
#: answers without a model AND tries to remember — the write a try must never make.
MODULE = {
    "conversations": [
        {"name": "Free Chat", "module_id": "FREE_CHAT", "content_id": "default",
         "prompt": "FREE CHAT PROMPT. Chat with {{ volley.config.child_pii.nickname }}.",
         "memory": {"namespace": "free_chat", "summarize": True, "min_volleys": 1}},
        {"name": "Bedtime", "module_id": "BEDTIME", "content_id": "default",
         "prompt": "BEDTIME PROMPT for {{ volley.config.child_pii.nickname }}: wind down."},
    ],
    "globals": [
        {"name": "Favourite Colour", "pattern": r"my favou?rite colou?r is (\w+)",
         "entity_groups": "1",
         "extension": {"ext_format": 1,
                       "capabilities": ["say", "handled", "memory.write"],
                       "on": "global",
                       "rules": [{"do": [{"remember": {"key": "colour", "value": "blue"}},
                                         {"say": "Ooh, I will remember that!"},
                                         {"handled": True}]}]}},
    ],
}


class APIConnectionError(Exception):
    """Named like openai's: `chat.is_offline_error` matches the class NAME."""


class AuthenticationError(Exception):
    status_code = 401


class BadRequest(Exception):
    status_code = 400


class FakeLLM:
    """The OpenAI client's `chat.completions` seam: answers `raw` (streamed in 7-char
    deltas, or whole) or raises a fresh `fail()`, and records every request it was sent."""

    def __init__(self, raw="", fail=None, delay=0.0):
        self.raw, self.fail, self.delay = raw, fail, delay
        self.requests = []
        self.done = threading.Event()

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kw):
        self.requests.append(kw)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.fail is not None:
                raise self.fail()
            if kw.get("stream"):
                return iter([{"choices": [{"delta": {"content": self.raw[i:i + 7]}}]}
                             for i in range(0, len(self.raw), 7)])
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=self.raw))])
        finally:
            self.done.set()

    def messages(self, i=-1):
        return self.requests[i]["messages"]


ENVELOPE = ('{"say": "Hi Sam! I love that idea. What should we draw first?", '
            '"mood": "happy", "gesture": "celebrate"}')


class Engines:
    """`config.BrainEngines` with scripted builders: the content and LLM brains are real
    apps over `FakeLLM`s; `webhook` cannot be built here (as with no endpoint set)."""

    def __init__(self, apps, *, pin=""):
        self.apps, self.pin, self.built = apps, pin, []

    def available(self):
        return {"available": brains.filter_options(brains.options(default="content"),
                                                   self.pin),
                "pin": self.pin, "pin_note": brains.pin_note(self.pin), "default": "content"}

    def build(self, name):
        self.built.append(name)
        if name == "webhook":
            raise SystemExit("MOXIE_APP=webhook requires MOXIE_WEBHOOK_ENDPOINT")
        return self.apps[name]


def _no_wait(_seconds):
    return None


@pytest.fixture
def no_backoff_sleep(monkeypatch):
    """Retries still happen (and are counted); only their waits are skipped."""
    real = chat_seam.call_with_backoff
    monkeypatch.setattr(chat_seam, "call_with_backoff",
                        lambda fn, **kw: real(fn, **dict(kw, sleep=_no_wait)))


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A supervisor whose robot runs the `content` brain, its status server, and the
    console app pointed at it."""
    monkeypatch.delenv("MOXIE_AUTHOR_TRY_BUDGET", raising=False)
    store = JsonStore(str(tmp_path / "data"))
    content_llm, free_llm = FakeLLM(raw="Sure thing, let's chat!"), FakeLLM(raw=ENVELOPE)
    # The brains' adaptive pacers slow down for real after a 429; the tests that provoke
    # one assert what it costs and says, not how long it takes.
    content = ContentApp(load_modules(MODULE),
                         chat_seam.make_openai_chat("http://127.0.0.1:1/v1", "k", "m",
                                                    client=content_llm,
                                                    pacer=chat_seam.Pacer(sleep=_no_wait)),
                         memory=MemoryStore(store), ext_grants=E.DEFAULT_GRANTS
                         | {"memory.write"})
    llm = LLMApp(base_url="http://127.0.0.1:1/v1", api_key="k", model="m", client=free_llm)
    llm._pacer = chat_seam.Pacer(sleep=_no_wait)
    rt, _ = make_runtime(content, device_id=DEVICE, store=store, module_id="FREE_CHAT",
                         content_id="default")
    engines = Engines({"content": content, "llm": llm, "echo": EchoApp()})
    rt.set_brain_engines(engines)
    base = status_server(rt)
    TestClient, main = console_app(tmp_path / "console.db", base + "/status")
    set_status_url(base + "/status", monkeypatch)
    with TestClient(main.app) as client:
        yield SimpleNamespace(rt=rt, base=base, client=client, store=store, engines=engines,
                              content_llm=content_llm, free_llm=free_llm, llm=llm,
                              root=tmp_path / "data")


def tryit(w, expect=200, **body):
    r = w.client.post("/local/tryit", json=body)
    assert r.status_code == expect, r.text
    return r.json()


def files_under(root) -> dict:
    """`{path: bytes}` for every file under `root` — the instrument for "wrote nothing"."""
    out = {}
    for d, _, names in os.walk(root):
        for n in names:
            p = os.path.join(d, n)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = fh.read()
    return out


# --- the contract: an answer from the robot's own brain, published nowhere -------------

def test_a_try_answers_through_the_robots_own_brain_and_publishes_nothing(world):
    out = tryit(world, speech="hi Moxie", device_id=DEVICE)
    assert out["ok"] is True and out["error"] is None
    assert out["preview"] is True and out["published"] is False
    assert out["brain"]["id"] == "content" and out["brain"]["source"] == "default"
    assert out["reply"]["text"] == "Sure thing, let's chat!"
    chunk, = out["reply"]["chunks"]
    assert chunk["markup"].endswith("/>") and "cmd:playback-mood" in chunk["markup"]
    assert chunk["perform"]["faces"], "the face the markup sets is read back for the card"
    assert chunk["scored"].get("mood"), chunk["scored"]
    assert out["model_calls"] == 1 and len(world.content_llm.requests) == 1
    assert out["history"] == [{"role": "user", "content": "hi Moxie"},
                              {"role": "assistant", "content": "Sure thing, let's chat!"}]
    assert world.rt.client.published == [], "a try published to MQTT"
    assert world.rt.history.get(DEVICE) in (None, []), "a try reached the robot's transcript"
    # The child the brain was told about is the robot's.
    system = world.content_llm.messages()[0]["content"]
    assert "Chat with Sam." in system


def test_the_options_say_who_answers_and_what_else_may(world):
    r = world.client.get(f"/local/tryit?device_id={DEVICE}")
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["ok"] is True and v["brain"]["id"] == "content"
    assert [b["id"] for b in v["brains"]] == ["llm", "content", "webhook", "echo"]
    assert [m["key"] for m in v["modules"]] == ["FREE_CHAT/default", "BEDTIME/default"]
    assert v["current_module"] == "FREE_CHAT/default"
    assert v["child"] == {"nickname": "Sam", "source": "robot"}
    assert v["limits"]["max_chars"] == 500 and v["budget"]["per_hour"] == 40
    assert world.free_llm.requests == [] and world.content_llm.requests == [], \
        "listing the choices called a model"
    # With no robot at all, the appliance's own child and default brain answer.
    v2 = world.client.get("/local/tryit").json()
    assert v2["ok"] is True and v2["device_id"] == "" and v2["child"]["source"] == "appliance"


def test_history_threads_into_the_next_turn(world):
    first = tryit(world, speech="hi Moxie", device_id=DEVICE)
    second = tryit(world, speech="tell me a joke", device_id=DEVICE,
                   history=first["history"])
    sent = world.content_llm.messages()
    assert [(m["role"], m["content"]) for m in sent[1:]] == [
        ("user", "hi Moxie"), ("assistant", "Sure thing, let's chat!"),
        ("user", "tell me a joke")]
    assert len(second["history"]) == 4 and second["history"][-1]["role"] == "assistant"


def test_the_session_is_cut_the_way_a_robots_transcript_is(world):
    world.rt._max_memory = 6
    long = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"line {i}"}
            for i in range(10)]
    out = tryit(world, speech="and now?", history=long)
    sent = world.content_llm.messages()
    assert [m["content"] for m in sent[1:-1]] == [f"line {i}" for i in range(4, 10)]
    assert out["history_trimmed"] == 4 and any("cut" in n for n in out["notes"])


# --- module, child and brain selection ----------------------------------------------------

def test_a_module_choice_reaches_the_content_brain(world):
    tryit(world, speech="night night", module="BEDTIME/default", nickname="Ada")
    assert world.content_llm.messages()[0]["content"].startswith(
        "BEDTIME PROMPT for Ada: wind down.")
    tryit(world, speech="hello", module="conversation:FREE_CHAT/default")
    assert world.content_llm.messages()[0]["content"].startswith("FREE CHAT PROMPT")
    bad = tryit(world, expect=400, speech="hi", module="NOPE/default")
    assert bad["kind"] == "unknown_module" and "NOPE/default" in bad["error"]


def test_a_brain_can_be_picked_for_one_try_without_changing_the_robot(world):
    out = tryit(world, speech="can we draw?", device_id=DEVICE, brain="llm")
    assert out["ok"] is True
    assert out["brain"] == {"id": "llm", "label": "Free-form companion (llm)",
                            "source": "picked", "note": ""}
    assert out["delivery"] == "stream" and world.free_llm.requests[0].get("stream")
    assert [c["text"] for c in out["reply"]["chunks"]] == [
        "Hi Sam! I love that idea.", "What should we draw first?"]
    assert out["reply"]["chunks"][0]["scored"]["mood"] == "happy"
    # The robot still answers with its own brain; nothing was written to its config.
    assert world.rt.brain_for(DEVICE)["brain"] == "content"
    assert world.rt._config_overrides.get(DEVICE) in (None, {})
    note = tryit(world, speech="hi", brain="echo", module="BEDTIME/default")
    assert note["reply"]["text"] == "You said: hi" and note["model_calls"] == 0
    assert any("does not read the activity" in n for n in note["notes"])


def test_a_pinned_appliance_refuses_another_brain_with_the_pin_sentence(world):
    world.engines.pin = "content"
    out = tryit(world, expect=400, speech="hi", brain="llm")
    assert out["kind"] == "bad_brain" and "MOXIE_APP" in out["error"], out
    assert world.free_llm.requests == []
    assert [b["id"] for b in world.client.get("/local/tryit").json()["brains"]] == ["content"]


def test_a_brain_that_cannot_be_built_is_a_readable_503(world):
    out = tryit(world, expect=503, speech="hi", brain="webhook")
    assert out["kind"] == "brain_unavailable"
    assert "MOXIE_WEBHOOK_ENDPOINT" in out["error"]


# --- actions: shown, never carried out -----------------------------------------------------

@pytest.mark.parametrize("say, kind, module", [
    ("<exit>Bye Sam! See you tomorrow.", "exit", ""),
    ("<launch:DRAW>Yes! Let's go make a picture.", "launch", "DRAW"),
    ("<sleep>Okay, nighty night.", "sleep", ""),
])
def test_an_action_is_surfaced_exactly_as_the_wire_would_carry_it(world, say, kind, module):
    world.free_llm.raw = json.dumps({"say": say, "mood": "happy"})
    out = tryit(world, speech="ok", device_id=DEVICE, brain="llm")
    action, = out["reply"]["actions"]
    assert action["type"] == kind and action["module_id"] == module
    assert action["wire"]["action"] == kind and action["wire"]["output_type"] == "GLOBAL"
    assert "<" not in out["reply"]["text"], "a tag was left in the spoken words"
    assert world.rt.client.published == [], "an action reached the robot"


# --- safety: the same classifier, nothing journaled ---------------------------------------

def test_a_blocked_line_is_redirected_without_the_brain_or_the_journal(world):
    out = tryit(world, speech="I want to kill myself", device_id=DEVICE)
    assert out["ok"] is True and out["model_calls"] == 0
    assert world.content_llm.requests == [], "a blocked line reached the brain"
    gate, = out["safety"]
    assert gate["stage"] == "input" and gate["action"] == "block"
    assert "self_harm" in gate["categories"]
    redirect = out["reply"]["text"]
    assert redirect and out["history"] == [{"role": "assistant", "content": redirect}], \
        "only Moxie's own line is remembered, as on a robot"
    assert world.rt.safety_view(DEVICE)["counts"] == {}, "a try was journaled for the child"


def test_an_unsafe_answer_is_replaced_before_anyone_sees_it_as_hers(world):
    world.content_llm.raw = "That is bullshit."
    out = tryit(world, speech="what do you think?")
    gate, = out["safety"]
    assert gate == {"stage": "output", "action": "block", "categories": ["profanity"],
                    "labels": gate["labels"], "escalate": False}
    assert "bullshit" not in out["reply"]["text"].lower() and out["reply"]["text"]


def test_an_unsafe_sentence_in_a_streamed_answer_is_replaced_and_ends_it(
        world, monkeypatch):
    """The streamed twin (the free brain streams): the blocked sentence becomes the
    redirect, nothing after it is spoken or remembered, and a robot running the same brain
    is sent exactly the same pieces. Which redirect line is a random pick, on a robot as
    here, so the pick is pinned to compare."""
    monkeypatch.setattr(safety_seam.random, "choice", lambda pool: pool[0])
    world.free_llm.raw = json.dumps({"say": "That is bullshit. Anyway, hi there!",
                                     "mood": "happy"})
    world.rt.update_config(DEVICE, brain="llm")
    out = tryit(world, speech="what do you think?", device_id=DEVICE)
    assert out["ok"] is True and out["delivery"] == "stream"
    gate, = out["safety"]
    assert gate["stage"] == "output" and gate["action"] == "block"
    chunk, = out["reply"]["chunks"]
    assert chunk["final"] is True and chunk["text"]
    assert "bullshit" not in json.dumps(out["reply"]).lower()
    assert "bullshit" not in json.dumps(out["history"]).lower()
    fresh_pool(world.rt)
    world.rt.client.published.clear()
    drive_turn(world.rt, DEVICE, "what do you think?")
    sent = world.rt.client.chat_replies(DEVICE)
    assert [(s["output"]["text"], s["output"].get("markup")) for s in sent] == \
        [(c["text"], c["markup"]) for c in out["reply"]["chunks"]]


# --- nothing written, anywhere -------------------------------------------------------------

def test_a_try_writes_nothing_and_a_real_turn_through_the_same_runtime_does(world):
    """The instrument proves itself: the same command, through a real turn, DOES write."""
    world.rt._memory_dir = str(world.root / "transcripts")
    before = files_under(world.root)
    for line in ("my favourite colour is blue", "hi Moxie"):
        out = tryit(world, speech=line, device_id=DEVICE, module="FREE_CHAT/default")
        assert out["ok"] is True, out
    assert out["model_calls"] == 1
    assert files_under(world.root) == before, "a try wrote to the store"
    fresh_pool(world.rt)
    drive_turn(world.rt, DEVICE, "my favourite colour is blue")
    after = files_under(world.root)
    assert after != before, "the control turn wrote nothing: this test proves nothing"
    assert any("memory" in p for p in after) and any("transcripts" in p for p in after)


# --- the same code path: a try and a published turn are the same staged answer ----------

def test_a_try_stages_exactly_what_a_robot_is_sent(world, monkeypatch):
    monkeypatch.setattr(world.rt, "_try_event_id", lambda: "evt-1")
    out = tryit(world, speech="hello there", module="FREE_CHAT/default")
    tried = world.content_llm.messages()
    fresh_pool(world.rt)
    sent = drive_turn(world.rt, DEVICE, "hello there", event_id="evt-1")
    assert world.content_llm.messages() == tried, "the brain was asked something else"
    chunk, = out["reply"]["chunks"]
    assert sent["output"]["text"] == chunk["text"]
    assert sent["output"]["markup"] == chunk["markup"]
    for field in ("mood", "dialog_act"):
        assert sent["output"].get(field) == chunk["scored"].get(field), field


def test_a_streamed_try_is_chunked_like_the_published_stream(world, monkeypatch):
    monkeypatch.setattr(world.rt, "_try_event_id", lambda: "evt-1")
    world.rt.update_config(DEVICE, brain="llm")
    out = tryit(world, speech="can we draw?", device_id=DEVICE)
    assert out["brain"]["source"] == "robot"
    fresh_pool(world.rt)
    world.rt.client.published.clear()
    drive_turn(world.rt, DEVICE, "can we draw?")
    sent = world.rt.client.chat_replies(DEVICE)
    tried = out["reply"]["chunks"]
    assert [s["output"]["text"] for s in sent] == [c["text"] for c in tried]
    assert len(tried) == 2, "the check below needs a piece past the first"
    for s, c in zip(sent, tried):
        for field in ("mood", "dialog_act"):
            assert s["output"].get(field) == c["scored"].get(field), (c["index"], field)
    assert world.free_llm.messages(0) == world.free_llm.messages(1)


def test_each_streamed_piece_is_staged_at_its_own_chunk_index(world, monkeypatch):
    """A brain that streams plain text leaves the performance to `_stage`, where the chunk
    index decides it (only the first piece plans a face): piece N of a try is staged as
    piece N of the published stream, so under one turn key the markup is byte-identical.
    (The expressive free brain writes its own markup, which the index does not touch.)"""
    plain = FakeLLM(raw="Hi Sam! I love that idea. What should we draw first?")
    llm = LLMApp(base_url="http://127.0.0.1:1/v1", api_key="k", model="m", client=plain,
                 expressive=False)
    llm._pacer = chat_seam.Pacer(sleep=_no_wait)
    world.engines.apps["llm"] = llm
    monkeypatch.setattr(world.rt, "_try_event_id", lambda: "evt-1")
    world.rt.update_config(DEVICE, brain="llm")
    out = tryit(world, speech="can we draw?", device_id=DEVICE)
    tried = out["reply"]["chunks"]
    assert out["delivery"] == "stream" and len(tried) == 2, tried
    fresh_pool(world.rt)
    world.rt.client.published.clear()
    drive_turn(world.rt, DEVICE, "can we draw?")
    sent = world.rt.client.chat_replies(DEVICE)
    assert [(s["output"]["text"], s["output"]["markup"]) for s in sent] == \
        [(c["text"], c["markup"]) for c in tried]
    assert "playback-mood" in tried[0]["markup"] and "playback-mood" not in tried[1]["markup"]


# --- errors a parent can read --------------------------------------------------------------

def test_an_unreachable_brain_is_a_502_that_keeps_the_session(world, no_backoff_sleep):
    world.free_llm.fail = lambda: APIConnectionError(
        "Connection refused by http://gateway.example.invalid:4000/v1")
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hey!"}]
    out = tryit(world, expect=502, speech="still there?", brain="llm", history=history)
    assert out["kind"] == "brain_unreachable" and out["result"] == "ERROR_OFFLINE"
    assert "could not be reached" in out["error"]
    assert out["detail"]["type"] == "APIConnectionError"
    assert out["history"] == history, "a failed try advanced the session"
    assert out["model_calls"] == 10, "the stream, its fallback and every retry count"
    assert "example.invalid" not in json.dumps(out), "the endpoint's address reached the page"


def test_a_refused_request_names_the_status_and_hides_the_key(world):
    token = "zz-0123456789abcdefghijKLMN"
    world.free_llm.fail = lambda: AuthenticationError(f"Incorrect API key provided: {token}")
    out = tryit(world, expect=502, speech="hi", brain="llm")
    assert out["kind"] == "brain_refused" and "HTTP 401" in out["error"]
    assert out["detail"]["status"] == 401 and out["detail"]["type"] == "AuthenticationError"
    assert token not in json.dumps(out) and "Incorrect API key" in out["detail"]["message"]
    assert out["reply"]["text"], "the line the child would have heard is still shown"
    assert out["model_calls"] == 2, "a 401 is not retried: the stream and its fallback"


@pytest.mark.parametrize("status, kind, fix", [
    (403, "brain_refused", "MOXIE_LLM_API_KEY"),
    (404, "brain_refused", "MOXIE_LLM_MODEL"),
    (429, "brain_refused", "rate-limiting this key"),
    (400, "brain_refused", "rejected the request (HTTP 400)"),
    (503, "brain_error", "failed on the request (HTTP 503)"),
])
def test_each_failure_says_what_to_fix(world, no_backoff_sleep, status, kind, fix):
    upstream = type("UpstreamError", (Exception,), {"status_code": status})
    world.content_llm.fail = lambda: upstream("nope")
    out = tryit(world, expect=502, speech="hi")
    assert out["kind"] == kind and fix in out["error"], out["error"]
    assert out["detail"]["status"] == status and out["history"] == []


def test_a_stream_that_will_not_open_then_a_fallback_that_answers_is_an_answer(
        world, monkeypatch):
    """A try reports how its LAST model request ended: the stream's refusal is spent once
    the single-reply fallback answers (`chat.last_call_error` is cleared by a success)."""
    real = world.free_llm.create

    def create(**kw):
        if kw.get("stream"):
            world.free_llm.requests.append(kw)
            raise BadRequest("streaming is not supported here")
        return real(**kw)
    monkeypatch.setattr(world.free_llm, "create", create)
    out = tryit(world, speech="hello", brain="llm")
    assert out["ok"] is True and out["kind"] == "" and out["error"] is None, out["error"]
    assert out["reply"]["text"] == "Hi Sam! I love that idea. What should we draw first?"
    assert out["model_calls"] == 2 and len(out["history"]) == 2


def test_a_brain_that_raises_shows_the_robots_own_stock_line_and_why(world, monkeypatch):
    """A crash is not a silent card: the child would hear the robot's `_safe_respond`
    line, so the try shows it beside what was raised; a real turn says the same line."""
    def boom(turn):
        raise TypeError("an app returned markup that is not text")
    monkeypatch.setattr(world.engines.apps["echo"], "respond", boom)
    out = tryit(world, expect=502, speech="hi", brain="echo")
    assert out["kind"] == "brain_error" and "raised TypeError" in out["error"]
    assert out["detail"]["type"] == "TypeError" and out["history"] == []
    said = out["reply"]["text"]
    assert said, "a crash must still show the line the child would have heard"
    world.rt.update_config(DEVICE, brain="echo")
    fresh_pool(world.rt)
    assert drive_turn(world.rt, DEVICE, "hi")["output"]["text"] == said


def test_a_cap_of_zero_is_named_as_a_setting_not_as_used_up(world, monkeypatch):
    monkeypatch.setenv("MOXIE_MODEL_CALL_LIMIT", "0")
    out = tryit(world, expect=502, speech="hi")
    assert out["kind"] == "brain_refused" and "not a positive whole number" in out["error"]
    assert "used up" not in out["error"]
    assert out["model_calls"] == 0 and world.content_llm.requests == []


def test_the_model_call_cap_is_named_and_the_try_is_not_charged(world, monkeypatch):
    monkeypatch.setenv("MOXIE_MODEL_CALL_LIMIT", str(max(1, chat_seam.model_calls())))
    if chat_seam.model_calls() == 0:
        chat_seam.note_model_call("chat")            # the cap needs one call behind it
    out = tryit(world, expect=502, speech="hi")
    assert out["kind"] == "brain_refused" and "MOXIE_MODEL_CALL_LIMIT" in out["error"]
    assert out["model_calls"] == 0 and world.content_llm.requests == []
    assert out["budget"]["remaining"] == out["budget"]["per_hour"], "a try that cost no " \
        "model call was still charged"


def test_a_brain_past_the_deadline_is_a_504_and_frees_its_slot(world):
    world.rt.TRY_TIMEOUT_S = 0.2
    world.content_llm.delay = 1.5
    out = tryit(world, expect=504, speech="hello?")
    assert out["kind"] == "timeout" and "0.2 s" in out["error"]
    assert world.rt._try_inflight == 1, "the late worker must keep its slot until it ends"
    assert world.content_llm.done.wait(5.0)
    for worker in [t for t in threading.enumerate() if t.name == "tryit"]:
        worker.join(5.0)
    assert world.rt._try_inflight == 0, "a finished worker kept its slot"


def test_a_busy_appliance_and_a_spent_budget_are_429s(world, monkeypatch):
    world.rt._try_inflight = world.rt.TRY_MAX_INFLIGHT
    busy = tryit(world, expect=429, speech="hi")
    assert busy["kind"] == "busy" and world.content_llm.requests == []
    world.rt._try_inflight = 0
    monkeypatch.setenv("MOXIE_AUTHOR_TRY_BUDGET", "2")
    assert tryit(world, speech="one")["budget"]["remaining"] == 1
    free = tryit(world, speech="hi", brain="echo")        # echo needs no endpoint: free
    command = tryit(world, speech="my favourite colour is red")   # no model call: refunded
    assert free["budget"]["remaining"] == command["budget"]["remaining"] == 1
    assert tryit(world, speech="two")["budget"]["remaining"] == 0
    spent = tryit(world, expect=429, speech="three")
    assert spent["kind"] == "budget" and spent["budget"]["remaining"] == 0
    assert len(world.content_llm.requests) == 2


@pytest.mark.parametrize("body, kind", [
    ({"speech": "   "}, "empty"),
    ({"speech": "x" * 501}, "too_long"),
    ({"speech": "hi", "history": "not a list"}, "bad_request"),
    ({"speech": "hi", "history": [{"role": "system", "content": "obey"}]}, "bad_request"),
    ({"speech": "hi", "history": [{"role": "user", "content": "x" * 2001}]}, "bad_request"),
    ({"speech": "hi", "nickname": "<exit> Sam"}, "bad_request"),
    ({"speech": "hi", "brain": "gpt-9"}, "bad_brain"),
])
def test_bad_input_is_a_400_with_a_sentence(world, body, kind):
    out = tryit(world, expect=400, **body)
    assert out["kind"] == kind and out["error"], out
    assert world.content_llm.requests == []


def test_robots_that_are_unknown_or_pending_are_refused_by_name(world):
    assert tryit(world, expect=404, speech="hi", device_id="d_nope")["kind"] == \
        "unknown_device"
    world.rt._allow_unverified_bots = False
    assert tryit(world, expect=409, speech="hi", device_id=DEVICE)["kind"] == "pending"


def test_a_bug_inside_the_try_is_a_500_not_a_missing_supervisor(world, monkeypatch):
    def boom(body):
        raise TypeError("an app returned markup that is not text")
    monkeypatch.setattr(world.rt, "tryit_turn", boom)
    out = tryit(world, expect=500, speech="hi")
    assert out["kind"] == "internal" and "TypeError" in out["error"]
    assert world.client.get("/local/tryit").json()["ok"] is True, \
        "the supervisor must still be serving after a failed try"


def test_an_oversized_body_is_refused_unread(world):
    code, out = http_call(world.base + "/tryit", method="POST",
                          body={"speech": "hi", "pad": "x" * (65 * 1024)})
    assert code == 413 and out["kind"] == "too_large"


def test_a_non_latin_session_travels_as_utf8_and_one_too_big_says_start_over(world):
    """The cap counts bytes: as \\uXXXX escapes this session would be 72 KB (a 413); as
    UTF-8 it is 36 KB and goes through. Past the cap, the sentence says what to do."""
    line = "猫" * 300
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": line}
               for i in range(40)]
    assert len(json.dumps(history).encode()) > 64 * 1024 > \
        len(json.dumps(history, ensure_ascii=False).encode())
    out = tryit(world, speech="hi", history=history)
    assert out["ok"] is True, out["error"]
    big = [dict(h, content="猫" * 700) for h in history]
    out = tryit(world, expect=413, speech="hi", history=big)
    assert out["kind"] == "too_large" and "Start over" in out["error"]


def test_the_card_says_so_when_the_supervisor_is_down_or_silent(world, monkeypatch):
    set_status_url(DEAD, monkeypatch)
    out = tryit(world, expect=503, speech="hi")
    assert out["kind"] == "unreachable" and out["published"] is False
    assert world.client.get("/local/tryit").json()["kind"] == "unreachable"

    class Silent(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            time.sleep(1.0)

    srv = HTTPServer(("127.0.0.1", 0), Silent)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from moxie_server.routes import console
        monkeypatch.setattr(console, "TRYIT_PROXY_TIMEOUT_S", 0.2)
        set_status_url(f"http://127.0.0.1:{srv.server_address[1]}/status", monkeypatch)
        slow = tryit(world, expect=504, speech="hi")
        assert slow["kind"] == "timeout" and "did not answer" in slow["error"]
    finally:
        srv.shutdown()
        srv.server_close()


# --- the card -----------------------------------------------------------------------------

def test_the_card_is_labelled_a_preview_and_its_one_brain_call_is_click_bound():
    html = open(os.path.join(REPO, "server", "static", "index.html")).read()
    start = html.index('id="tryit-card"')
    card = html[start:html.index("</section>", start)].lower()
    assert "preview" in card and "nothing is sent to a robot" in card
    for control in ("try-brain", "try-module", "try-name", "try-text", "btn-try-send",
                    "btn-try-reset", "try-log"):
        assert f'id="{control}"' in card, control
    assert '<script src="/js/tryit.js"></script>' in html
    js = open(os.path.join(REPO, "server", "static", "js", "tryit.js")).read()
    assert "setTimeout(" not in js and "setInterval(" not in js, \
        "a timer in the Try it card could reach a model"
    calls = [m.start() for m in re.finditer(r"postJson\('/local/tryit'", js)]
    assert len(calls) == 1, "the brain must have exactly one call site"
    body = js[js.index("async function trySend("):]
    assert calls[0] > js.index("async function trySend(") and \
        calls[0] < js.index("async function trySend(") + len(body)
    assert re.search(r"\.onclick\s*=\s*trySend", js), "Send is not bound to a click"
    assert "tryit.js" in console_js() or "trySend" in console_js()


def test_the_views_survive_a_garbled_payload():
    from moxie_server import fleet
    for bad in (None, {}, {"ok": "yes", "reply": [], "history": "x", "safety": 3},
                {"ok": True, "reply": {"chunks": [{"perform": "x", "actions": [1]}]}}):
        out = fleet.normalize_tryit(bad)
        assert out["published"] is False and isinstance(out["reply"]["chunks"], list)
        assert isinstance(fleet.normalize_tryit_options(bad)["modules"], list)
