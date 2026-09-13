"""
Live action tags — does the REAL model actually drive the robot?

`test_action_tags.py` proves the parser, the apps and the wire with canned model
text: given a tagged line, everything downstream is correct. It cannot prove the one
thing that matters at the top of the stack — whether the configured model, gateway,
and shipped prompt produce a tag in this bounded sample. Historical samples observed
0/3 goodbye actions and 0/2 activity actions; they did not isolate a prompt cause.

What is asserted here is a bounded acceptance sample: `_ACCEPT` of `_TRIALS`
goodbye turns must lift a real `<exit>` action off the model's own text, and likewise
for `<launch:...>`. A rate is the honest shape for a temperature-0.8 model — a
1-of-1 assertion would be a coin flip dressed as a test, and demanding 3/3 of a
sampling model would make the suite flap. The threshold is deliberately well above
the measured 0/N historical sample. It is not a population adherence estimate.

Runs only with a gateway key (`MOXIE_LLM_API_KEY` / `LITELLM_MASTER_KEY`, e.g. from
the git-ignored `mqtt/.env`); skips cleanly otherwise. Retries and the other two tests
make the whole file unsafe as one bounded probe. Use `sim/tools/run_live_action_tags.sh`,
which selects only the three-trial goodbye rate check, refuses a seventh request attempt,
and gives the campaign one deadline. Activity adherence needs its own later budget.
"""
import os
import sys
from importlib.util import find_spec

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "sim"))
sys.path.insert(0, os.path.dirname(__file__))

from helpers_runtime import load_repo_dotenv  # noqa: E402

load_repo_dotenv()          # mqtt/.env from this tree or the main checkout
KEY = os.environ.get("MOXIE_LLM_API_KEY") or os.environ.get("LITELLM_MASTER_KEY") or ""
BASE = os.environ.get("MOXIE_LLM_BASE_URL", "https://gateway.graphlings.net/v1")
MODEL = os.environ.get("MOXIE_LLM_MODEL", "graphling-medium")

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


def _app(record=None):
    pytest.importorskip("openai")
    from moxie_sdk.apps import LLMApp
    if record is None:
        return LLMApp(base_url=BASE, api_key=KEY, model=MODEL, max_tokens=160)
    from openai import OpenAI
    from tools.action_tag_campaign import RecordingClient
    client = RecordingClient(
        OpenAI(base_url=BASE, api_key=KEY or "sk-local", max_retries=0), record)
    return LLMApp(base_url=BASE, api_key=KEY, model=MODEL, max_tokens=160, client=client)


def _robot():
    from moxie_sdk.types import ChildProfile, RobotContext
    return RobotContext(device_id="d_live_tags", child=ChildProfile(nickname="Sam"))


def _run(speeches, record=None, history=None):
    """Drive completed real turns; stop before a fallback can become a sample."""
    from moxie_sdk.types import Turn
    app, robot = _app(record), _robot()
    out = []
    for speech in speeches[:_TRIALS]:
        before = record.completed if record else None
        reply = app.respond(Turn(robot=robot, speech=speech,
                                 history=list(history or [])))
        if record and record.completed == before:
            break
        out.append(reply if record else (speech, reply))
    return out


def _report(label, results, hits):
    """Legacy/manual tests also emit counts only; model output is untrusted."""
    print(f"\n[live tags] {label}: {hits}/{len(results)}")


def test_the_model_ends_a_goodbye_with_a_real_exit_action(request):
    """A goodbye turn must produce ActionType.EXIT off the model's own text —
    the action the runtime puts on the wire as `response_actions`."""
    from moxie_sdk.chat import model_calls, reset_model_calls
    from moxie_sdk.types import ActionType
    from tools.action_tag_campaign import CampaignRecord
    record = CampaignRecord(
        "goodbye", _TRIALS, 6,
        request.config.getoption("--moxie-campaign-state-file") or None)
    if not KEY or find_spec("openai") is None:
        record.skipped()
        pytest.skip("missing live-campaign prerequisite")
    reset_model_calls()
    results = _run(GOODBYES, record)
    for reply in results:
        exit_hit = any(a.type is ActionType.EXIT for a in reply.actions)
        record.trial(exit_hit=exit_hit,
                     output_ok=bool(reply.text.strip()) and "<" not in reply.text)
    summary = record.finish(model_calls())
    assert summary["measurement"] == "completed", "goodbye campaign was incomplete"
    assert summary["adherence"] == "pass", "completed goodbye campaign missed acceptance"


@pytest.mark.skipif(not KEY, reason="missing live-test gateway key")
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
            assert a.module_id == "DRAW", "model selected an unexpected module"
    for _, reply in results:
        assert "<" not in reply.text, "an action tag leaked into speech"
    assert hits >= _ACCEPT, (
        f"only {hits}/{len(results)} activity turns emitted <launch:DRAW>; the model "
        f"has stopped following the action-tag prompt in LLMApp._system")


@pytest.mark.skipif(not KEY, reason="missing live-test gateway key")
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
    assert ra[0]["action"] == ActionType.EXIT.value, "unexpected wire action"
    assert ra[0]["output_type"] == "GLOBAL", "unexpected wire action scope"
    assert "<" not in resp["output"]["text"], "an action tag leaked into wire speech"
    assert "<exit>" not in resp["output"]["markup"], "an action tag leaked into markup"
    print(f"\n[live tags] wire: action_count={len(ra)}")
