"""
Live action tags — does the REAL model actually drive the robot?

`test_action_tags.py` proves the parser, the apps and the wire with canned model
text: given a tagged line, everything downstream is correct. It cannot prove the one
thing that matters at the top of the stack — that the brain we ship against
(graphling-medium, through our LiteLLM gateway) *emits* a tag when it should. When
this file was written it emphatically did not: **0/3 goodbye turns and 0/2 activity
turns produced any action** with the prompt as shipped. That is a prompt problem, and
this file is how it stays fixed.

What is asserted here is a RATE, not a single lucky sample: `_ACCEPT` of `_TRIALS`
goodbye turns must lift a real `<exit>` action off the model's own text, and likewise
for `<launch:...>`. A rate is the honest shape for a temperature-0.8 model — a
1-of-1 assertion would be a coin flip dressed as a test, and demanding 3/3 of a
sampling model would make the suite flap. The threshold is deliberately well above
the measured 0/N baseline and at/below what the tuned prompt sustains.

Runs only with a gateway key (`MOXIE_LLM_API_KEY` / `LITELLM_MASTER_KEY`, e.g. from
the git-ignored `mqtt/.env`); skips cleanly otherwise. Costs `2 * _TRIALS` gateway
calls, so `_TRIALS` stays small and the SDK's own backoff/pacing does the throttling.
"""
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))


def _load_dotenv():
    try:
        for line in open(os.path.join(REPO, "mqtt", ".env")):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


_load_dotenv()
KEY = os.environ.get("MOXIE_LLM_API_KEY") or os.environ.get("LITELLM_MASTER_KEY") or ""
BASE = os.environ.get("MOXIE_LLM_BASE_URL", "https://gateway.graphlings.net/v1")
MODEL = os.environ.get("MOXIE_LLM_MODEL", "graphling-medium")

pytestmark = pytest.mark.skipif(
    not KEY, reason="no gateway key (set MOXIE_LLM_API_KEY in mqtt/.env for live tests)")

# How many turns we sample, and how many of them must carry the action.
_TRIALS = 3
_ACCEPT = 2

GOODBYES = [
    "bye moxie, I have to go now",
    "okay I'm all done talking, goodbye!",
    "that's enough for today, bye!",
]
# The tag grammar only permits a module the model "has actually been told about in
# this conversation" — so the activity is introduced in the history, exactly the way
# a real content module would have introduced it a turn earlier.
DRAW_HISTORY = [
    {"role": "user", "content": "what can we do together?"},
    {"role": "assistant",
     "content": "We could play the DRAW activity — it's a drawing game! Or we can just chat."},
]
LAUNCHES = [
    "yes! let's draw",
    "can we do the DRAW activity now please",
    "I want to draw a picture with you",
]


def _app():
    pytest.importorskip("openai")
    from moxie_sdk.apps import LLMApp
    return LLMApp(base_url=BASE, api_key=KEY, model=MODEL, max_tokens=160)


def _robot():
    from moxie_sdk.types import ChildProfile, RobotContext
    return RobotContext(device_id="d_live_tags", child=ChildProfile(nickname="Sam"))


def _run(speeches, history=None):
    """Drive N real turns; return [(text, [Action]), ...]."""
    from moxie_sdk.types import Turn
    app, robot = _app(), _robot()
    out = []
    for speech in speeches[:_TRIALS]:
        reply = app.respond(Turn(robot=robot, speech=speech,
                                 history=list(history or [])))
        out.append((speech, reply))
    return out


def _report(label, results, hits):
    lines = [f"\n[live tags] {label}: {hits}/{len(results)}"]
    for speech, reply in results:
        kinds = [(a.type.value, a.module_id) for a in reply.actions]
        lines.append(f"    {speech!r} -> {reply.text!r} {kinds}")
    print("\n".join(lines))


def test_the_model_ends_a_goodbye_with_a_real_exit_action():
    """A goodbye turn must produce ActionType.EXIT off the model's own text —
    the action the runtime puts on the wire as `response_actions`."""
    from moxie_sdk.types import ActionType
    results = _run(GOODBYES)
    hits = sum(1 for _, r in results
               if any(a.type is ActionType.EXIT for a in r.actions))
    _report("goodbye -> <exit>", results, hits)
    for _, reply in results:
        assert "<" not in reply.text, f"a tag leaked into speech: {reply.text!r}"
        assert reply.text.strip(), "goodbye turn produced no spoken line"
    assert hits >= _ACCEPT, (
        f"only {hits}/{len(results)} goodbye turns emitted <exit>; the model has "
        f"stopped following the action-tag prompt in LLMApp._system")


def test_the_model_launches_an_activity_it_was_told_about():
    """An activity request must produce a LAUNCH action naming the module the
    conversation introduced — and no other module."""
    from moxie_sdk.types import ActionType
    results = _run(LAUNCHES, history=DRAW_HISTORY)
    launches = [[a for a in r.actions if a.type is ActionType.LAUNCH]
                for _, r in results]
    hits = sum(1 for L in launches if L)
    _report("activity -> <launch:DRAW>", results, hits)
    for L in launches:
        for a in L:
            assert a.module_id == "DRAW", f"launched a module nobody mentioned: {a}"
    for _, reply in results:
        assert "<" not in reply.text, f"a tag leaked into speech: {reply.text!r}"
    assert hits >= _ACCEPT, (
        f"only {hits}/{len(results)} activity turns emitted <launch:DRAW>; the model "
        f"has stopped following the action-tag prompt in LLMApp._system")


def test_a_tagged_live_turn_reaches_the_wire_as_response_actions():
    """The whole seam in one go: a real model turn through the real MoxieRuntime, with
    the action arriving on the wire as a spec `RemoteChatAction`. Skipped (not failed)
    when this particular sample happens not to carry a tag — the RATE tests above are
    where model compliance is judged; this one is about the plumbing under it."""
    pytest.importorskip("paho.mqtt.client")
    sys.path.insert(0, os.path.dirname(__file__))
    from helpers_runtime import assert_spec_response, drive_once
    from moxie_sdk.types import ActionType

    app = _app()
    resp = None
    for speech in GOODBYES[:2]:
        resp = drive_once(app, speech, device_id="d_live_wire",
                          module_id="FREE_CHAT", content_id="default",
                          event_id="evt-live-tag")
        assert_spec_response(resp, event_id="evt-live-tag")
        if resp.get("response_actions"):
            break
    else:
        pytest.skip("this sample carried no tag; see the rate tests for compliance")
    ra = resp["response_actions"]
    assert ra[0]["action"] == ActionType.EXIT.value, ra
    assert ra[0]["output_type"] == "GLOBAL", ra
    assert "<" not in resp["output"]["text"], resp
    assert "<exit>" not in resp["output"]["markup"], resp
    print(f"\n[live tags] wire: {resp['output']['text']!r} actions={ra}")
