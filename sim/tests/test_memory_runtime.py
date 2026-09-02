"""
Memory through the REAL runtime — the end-of-conversation hook and the parent's
read/erase endpoints.

`test_memory.py` covers the store and the summarizer in isolation. This suite proves
the two things only the runtime can do:

  * it **notices a conversation ended** — an `<exit>` in the answer, a module switch, or
    the robot going offline — and calls `MoxieApp.on_session_end`, which is where
    long-term memory gets written (the contract's `complete_handler` moment);
  * it **serves the memory to a parent**: `GET /memory` (what Moxie remembers, by
    namespace, with provenance) and `DELETE` / `POST /memory` (erase one namespace or
    everything) on the same localhost-only status server as `/status` and `/safety`.
    That is BEYOND #4's floor (openmoxie-feature-audit.md §4.2): a memory a parent
    cannot read or erase is not acceptable on a child's device.

Hermetic: fake MQTT transport, fake brain, tmp storage, no sleeps, no `openai`.
"""
import json
import os
import socket
import sys
import urllib.error
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))

from helpers_runtime import CHAT_TOPIC, LatchClient, make_runtime  # noqa: E402
import moxie_runtime  # noqa: E402
from moxie_sdk.app import MoxieApp  # noqa: E402
from moxie_sdk.cloud_config import LoggingPolicy  # noqa: E402
from moxie_sdk.content import ContentApp, load_module  # noqa: E402
from moxie_sdk.store import JsonStore, MemoryStore, item_text  # noqa: E402
from moxie_sdk.types import Reply  # noqa: E402

MODULE = {
    "conversations": [{
        "name": "Memory Chat", "module_id": "MCHAT", "content_id": "default",
        "prompt": "Talking to {{ volley.config.child_pii.nickname }}.\n"
                  "FACTS:\n{{ volley.persist_data.mchat.facts }}",
        "memory": {"namespace": "mchat", "summarize": True, "min_volleys": 2},
    }],
}

SUMMARY = json.dumps({"facts": ["Sam has a dog named Pepper"],
                      "preferences": [], "open_threads": [],
                      "summary": "They talked about pets."})


def texts(values):
    """The sentences of a stored list — items are `{id, text, …}` records now."""
    return [item_text(v) for v in values or []]


def _brain(answer="Okay!", record=None):
    """A fake brain that answers turns normally and returns our canned JSON when it is
    asked to summarize (the summarization prompt is the one that asks for JSON).

    Saying "bye" gets an answer carrying `<exit>` — which is how a real module ends a
    conversation, and therefore how this suite reaches the memory hook."""
    def chat(messages):
        text = messages[0]["content"]
        if record is not None:
            record.append(text)
        if "JSON object" in text:
            return SUMMARY
        if "bye" in (messages[-1].get("content") or "").lower():
            return "That was fun!<exit>"
        return answer
    return chat


class _RecordingApp(MoxieApp):
    """Answers with a fixed line and records every `on_session_end` it is told about."""
    name = "recording"

    def __init__(self, text="Okay!"):
        self.text = text
        self.ended = []

    def respond(self, turn):
        from moxie_sdk.actions import parse_action_tags
        text, actions = parse_action_tags(self.text)
        return Reply(text=text, actions=actions)

    def on_session_end(self, robot, history, reason=""):
        self.ended.append({"device_id": robot.device_id, "reason": reason,
                           "turns": len(history)})


def _runtime(app, **kw):
    rt, device_id = make_runtime(app, module_id="MCHAT", **kw)
    rt.client = LatchClient()
    return rt, device_id


def _drive(rt, device_id, speech, *, module_id=None, event_id="evt"):
    """One turn, waiting for its published reply instead of sleeping."""
    topic = CHAT_TOPIC.format(device_id=device_id)
    before = len(rt.client.on(topic))
    payload = {"command": "prompt", "backend": "router", "event_id": event_id,
               "speech": speech}
    if module_id:
        payload["module_id"] = module_id
    rt._on_remote_chat(device_id, rt.robots[device_id], json.dumps(payload))
    assert rt.client.wait_for(
        lambda pubs: len([1 for t, _ in pubs if t == topic]) > before, timeout=15), \
        f"no reply published for {speech!r}"


def _content_runtime(tmp_path, answer="Okay!", record=None):
    app = ContentApp(load_module(MODULE), _brain(answer, record),
                     memory=MemoryStore(JsonStore(str(tmp_path))))
    return _runtime(app) + (app,)


# ---------------------------------------------------------------------------
# the runtime notices a conversation ended
# ---------------------------------------------------------------------------

def test_exit_tag_ends_the_conversation():
    """`<exit>` in the model's own line ends the activity → on_session_end("exit")."""
    app = _RecordingApp("That was fun!<exit>")
    rt, did = _runtime(app)
    _drive(rt, did, "bye")
    rt._pool.shutdown(wait=True)
    assert app.ended == [{"device_id": did, "reason": "exit", "turns": 2}]


def test_a_plain_answer_does_not_end_the_conversation():
    app = _RecordingApp("Tell me more!")
    rt, did = _runtime(app)
    _drive(rt, did, "hi")
    rt._pool.shutdown(wait=True)
    assert app.ended == []


def test_module_switch_ends_the_previous_conversation():
    app = _RecordingApp("Okay!")
    rt, did = _runtime(app)
    _drive(rt, did, "hi", module_id="MCHAT")
    _drive(rt, did, "let's draw", module_id="DRAW", event_id="evt-2")
    rt._pool.shutdown(wait=True)
    assert [e["reason"] for e in app.ended] == ["module_switch"]
    assert rt.robots[did].module_id == "DRAW"


def test_disconnect_ends_the_conversation():
    app = _RecordingApp("Okay!")
    rt, did = _runtime(app)
    _drive(rt, did, "hi")
    rt._device_disconnect(did)
    rt._pool.shutdown(wait=True)
    assert [e["reason"] for e in app.ended] == ["disconnect"]


def test_a_session_end_that_raises_never_breaks_the_runtime():
    class Boom(_RecordingApp):
        def on_session_end(self, robot, history, reason=""):
            raise RuntimeError("summarizer exploded")
    rt, did = _runtime(Boom("Bye!<exit>"))
    _drive(rt, did, "bye")
    rt._pool.shutdown(wait=True)          # the turn still published its reply
    assert rt.client.chat_replies(did)[-1]["output"]["text"] == "Bye!"


# ---------------------------------------------------------------------------
# ...and the memory actually gets written, through the whole stack
# ---------------------------------------------------------------------------

def test_a_finished_conversation_is_remembered(tmp_path):
    rt, did, app = _content_runtime(tmp_path, answer="Nice!")
    _drive(rt, did, "I have a dog", event_id="e1")
    _drive(rt, did, "her name is Pepper, bye!", event_id="e2")
    rt._pool.shutdown(wait=True)
    assert rt.client.chat_replies(did)[-1]["output"]["text"] == "That was fun!"
    block = app.memory.load(did)["mchat"]
    assert texts(block["facts"]) == ["Sam has a dog named Pepper"]
    assert texts(block["summaries"]) == ["They talked about pets."]
    assert block["_provenance"][0]["reason"] == "exit"
    assert block["facts"][0]["id"] and block["facts"][0]["_provenance"]["reason"] == "exit"


def test_the_next_conversation_recalls_the_fact(tmp_path):
    """End to end: what conversation #1 learned is in conversation #2's system prompt."""
    rt, did, app = _content_runtime(tmp_path, answer="Nice!")
    _drive(rt, did, "I have a dog", event_id="e1")
    _drive(rt, did, "her name is Pepper", event_id="e2")
    rt._end_conversation(did, "exit", inline=True)
    assert texts(app.memory.load(did)["mchat"]["facts"]) == ["Sam has a dog named Pepper"]

    prompts = []
    app2 = ContentApp(load_module(MODULE), _brain("Hi again!", prompts),
                      memory=MemoryStore(JsonStore(str(tmp_path))))
    rt2, _ = _runtime(app2, device_id=did)
    _drive(rt2, did, "hello again")
    rt2._pool.shutdown(wait=True)
    assert any("- Sam has a dog named Pepper" in p for p in prompts), prompts


def test_no_data_policy_from_the_parent_stops_memory_being_written(tmp_path):
    rt, did, app = _content_runtime(tmp_path, answer="Nice!")
    # The runtime installs its own per-device gate on the app's store, so a parent's
    # `logging_policy` choice reaches memory without the app knowing about devices.
    assert app.memory.policy == rt.memory_policy
    assert rt.memory_policy(did) == moxie_runtime.MEMORY_POLICY
    rt._config_overrides[did] = {"logging_policy": int(LoggingPolicy.NO_DATA)}
    assert rt.memory_policy(did) == LoggingPolicy.NO_DATA
    _drive(rt, did, "I have a dog", event_id="e1")
    _drive(rt, did, "her name is Pepper, bye!", event_id="e2")
    rt._pool.shutdown(wait=True)
    assert app.memory.load(did) == {}


# ---------------------------------------------------------------------------
# the parent's read + erase
# ---------------------------------------------------------------------------

def test_a_fleet_wide_no_data_rule_also_stops_memory(tmp_path):
    """A house rule set once for the appliance (fleet config) turns memory off for every
    robot on it — `memory_policy` reads the effective `fleet ⊕ per-robot` layer."""
    rt, did, app = _content_runtime(tmp_path)
    rt.store = JsonStore(str(tmp_path / "fleet"))
    rt.update_fleet_config(logging_policy=int(LoggingPolicy.NO_DATA))
    assert rt.memory_policy(did) == LoggingPolicy.NO_DATA
    assert app.memory.writes_allowed(did) is False
    # ...and one robot can still be set apart from the house rule
    rt._config_overrides[did] = {"logging_policy": int(LoggingPolicy.NO_MEDIA)}
    assert rt.memory_policy(did) == LoggingPolicy.NO_MEDIA


def test_memory_view_and_erase_by_namespace(tmp_path):
    rt, did, app = _content_runtime(tmp_path)
    app.memory.merge(did, "mchat", {"facts": ["has a dog"]},
                     provenance={"module_id": "MCHAT", "turns": 3})
    app.memory.merge(did, "free_chat", {"facts": ["likes red"]})
    view = rt.memory_view(did)
    assert view["ok"] and view["policy"] == "NO_MEDIA"
    assert texts(view["namespaces"]["mchat"]["data"]["facts"]) == ["has a dog"]
    assert view["namespaces"]["mchat"]["provenance"][0]["turns"] == 3
    assert view["bytes"] > 0 and view["writes_allowed"] is True

    assert rt.erase_memory(did, "mchat")["erased"] is True
    assert list(rt.memory_view(did)["namespaces"]) == ["free_chat"]
    out = rt.erase_memory(did)                      # everything
    assert out["ok"] and out["erased"] is True and out["namespaces"] == {}


def test_memory_view_404s_for_an_unknown_device(tmp_path):
    rt, _did, _app = _content_runtime(tmp_path)
    out = rt.memory_view("d_nope")
    assert out["ok"] is False and "unknown device_id" in out["error"]


def test_memory_endpoints_over_http(tmp_path):
    """GET /memory, DELETE /memory and POST /memory on the real status server."""
    rt, did, app = _content_runtime(tmp_path)
    app.memory.merge(did, "mchat", {"facts": ["has a dog"]},
                     provenance={"module_id": "MCHAT", "turns": 2})
    app.memory.merge(did, "free_chat", {"facts": ["likes red"]})
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    rt._start_status_server(port)
    base = f"http://127.0.0.1:{port}"

    def _req(path, method="GET", body=None):
        req = urllib.request.Request(base + path, method=method,
                                     data=json.dumps(body).encode() if body else None)
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())

    view = _req(f"/memory?device_id={did}")
    assert view["ok"] and set(view["namespaces"]) == {"mchat", "free_chat"}
    assert view["namespaces"]["mchat"]["provenance"][0]["module_id"] == "MCHAT"

    gone = _req(f"/memory?device_id={did}&namespace=mchat", method="DELETE")
    assert gone["erased"] is True and list(gone["namespaces"]) == ["free_chat"]

    cleared = _req(f"/memory?device_id={did}", method="POST", body={"erase": "all"})
    assert cleared["ok"] and cleared["namespaces"] == {}
    assert app.memory.load(did) == {}

    try:
        _req("/memory?device_id=d_missing")
        assert False, "unknown device should 404"
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_per_item_erase_and_edit_through_the_runtime(tmp_path):
    """BEYOND #4's other half, at the runtime seam: one wrong line goes, or gets fixed,
    without costing the rest of what that activity learned."""
    rt, did, app = _content_runtime(tmp_path)
    app.memory.merge(did, "mchat",
                     {"facts": ["Puppy sleeps on his bed", "Sam is in year 2"]},
                     provenance={"module_id": "MCHAT", "turns": 3, "at": 1788352646.0},
                     meta={"summarized_through": 6}, now=1788352646.0)
    wrong, other = [f["id"] for f in app.memory.load(did)["mchat"]["facts"]]

    fixed = rt.edit_memory_item(did, "mchat", wrong, "Puppy sleeps on my bed")
    assert fixed["ok"] and fixed["edited"] is True and fixed["item"] == wrong
    stored = app.memory.load(did)["mchat"]["facts"][0]
    assert stored["text"] == "Puppy sleeps on my bed" and stored["pinned"] is True
    assert stored["id"] == wrong                       # the id survives a correction
    # the parent view carries ids and `summarized_through` for the console card
    ns = fixed["namespaces"]["mchat"]
    assert ns["meta"] == {"summarized_through": 6}
    assert [f["id"] for f in ns["data"]["facts"]] == [wrong, other]

    gone = rt.erase_memory(did, "mchat", other)
    assert gone["ok"] and gone["erased"] is True and gone["item"] == other
    assert texts(app.memory.load(did)["mchat"]["facts"]) == ["Puppy sleeps on my bed"]
    assert app.memory.load(did)["mchat"]["_meta"] == {"summarized_through": 6}
    assert rt.erase_memory(did, "mchat", "nope")["erased"] is False


def test_a_refused_edit_changes_nothing(tmp_path):
    rt, did, app = _content_runtime(tmp_path)
    app.memory.merge(did, "mchat", {"facts": ["has a dog"]}, provenance={"turns": 1})
    one = app.memory.load(did)["mchat"]["facts"][0]["id"]
    # the runtime hands the store this robot's own transcript, so a parent pasting the
    # child's words back in is refused the same way a model quoting them would be
    rt.history[did] = [{"role": "user",
                        "content": "my grandma lives on Elm Street in the yellow house"}]
    for bad in ("I want to kill myself",
                "my grandma lives on Elm Street in the yellow house", ""):
        try:
            rt.edit_memory_item(did, "mchat", one, bad)
            assert False, f"{bad!r} should have been refused"
        except ValueError as e:
            assert str(e)
    assert texts(app.memory.load(did)["mchat"]["facts"]) == ["has a dog"]


def test_per_item_endpoints_over_http(tmp_path):
    """`DELETE /memory?...&item=` and `POST /memory {"edit": …}` on the real server."""
    rt, did, app = _content_runtime(tmp_path)
    app.memory.merge(did, "mchat", {"facts": ["Puppy sleeps on his bed", "likes red"]},
                     provenance={"module_id": "MCHAT", "turns": 2})
    wrong, other = [f["id"] for f in app.memory.load(did)["mchat"]["facts"]]
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    rt._start_status_server(port)
    base = f"http://127.0.0.1:{port}"

    def _req(path, method="GET", body=None):
        req = urllib.request.Request(base + path, method=method,
                                     data=json.dumps(body).encode() if body else None)
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())

    out = _req(f"/memory?device_id={did}", method="POST",
               body={"edit": {"namespace": "mchat", "item": wrong,
                              "text": "Puppy sleeps on my bed"}})
    assert out["ok"] and out["edited"] is True
    assert texts(app.memory.load(did)["mchat"]["facts"])[0] == "Puppy sleeps on my bed"

    out = _req(f"/memory?device_id={did}&namespace=mchat&item={other}", method="DELETE")
    assert out["erased"] is True and out["item"] == other
    assert texts(app.memory.load(did)["mchat"]["facts"]) == ["Puppy sleeps on my bed"]

    try:
        _req(f"/memory?device_id={did}", method="POST",
             body={"edit": {"namespace": "mchat", "item": wrong,
                            "text": "I want to kill myself"}})
        assert False, "an unsafe correction must be refused"
    except urllib.error.HTTPError as e:
        assert e.code == 400 and json.loads(e.read().decode())["ok"] is False


def test_memory_endpoint_answers_for_an_app_without_a_memory_store(tmp_path):
    """A non-content app still gets a parent-readable memory endpoint (empty, but real)."""
    rt, did = _runtime(_RecordingApp())
    rt.store = JsonStore(str(tmp_path))
    assert rt.memory_view(did) == {"ok": True, "device_id": did, "namespaces": {},
                                   "bytes": 0, "writes_allowed": True,
                                   "policy": "NO_MEDIA"}
