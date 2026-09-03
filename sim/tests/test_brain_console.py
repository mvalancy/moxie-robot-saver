"""
🧠 The brain picker's console layer — the normalizer between the supervisor and the card.

`server/moxie_server/fleet.py::normalize_brain` is the only thing standing between a
runtime payload and a parent's screen, and its contract is defensive rather than clever:
**a card must never be a 500, and it must never look empty when the truth is "unreachable"**
— an empty dropdown reads as *"this appliance has no brains"*, which is a different and
much worse claim than *"the supervisor is down"*.

So this file feeds it the shapes the real world produces — a live payload, a refusal, a
supervisor that never answered, a truncated body, a payload from a newer supervisor with
fields this console has never heard of — and asserts the card can render every one.

Pure: `fleet.py` imports nothing from `fastapi` and nothing from `mqtt/`, which is what
lets it be tested in the hermetic tier at all (the two processes do not share a path).
The live end-to-end — console → supervisor → registry — is `test_brain_runtime.py`'s
HTTP section plus the console round trip.
"""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "server"))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk import brains                                          # noqa: E402
from moxie_server.fleet import (normalize_brain,                      # noqa: E402
                                normalize_brain_option,
                                normalize_brain_robot)

#: What `MoxieRuntime.brain_view()` really answers, trimmed to the fields the card reads.
LIVE = {
    "ok": True,
    "available": [{"id": "llm", "label": "Free-form companion", "group": "Conversation",
                   "blurb": "An OpenAI-compatible model…", "needs": ["MOXIE_LLM_BASE_URL"],
                   "default": True},
                  {"id": "echo", "label": "Echo (no model)", "group": "Built-in",
                   "blurb": "Repeats what it hears.", "needs": [], "default": False}],
    "pin": "", "pin_note": "", "default": "llm", "fleet": "content",
    "appliance": "llm", "installed": ["echo", "llm"], "env_var": "MOXIE_APP",
    "robots": [{"device_id": "d_one", "child": "Sam", "brain": "content",
                "source": "fleet", "requested": "", "note": "", "override": "",
                "label": "Content modules (content)",
                "line": "brain: d_one: content (house rule)"}],
}


def test_a_live_payload_survives_intact():
    out = normalize_brain(LIVE)
    assert out["ok"] is True and out["error"] is None
    assert [e["id"] for e in out["available"]] == ["llm", "echo"]
    assert out["available"][0]["needs"] == ["MOXIE_LLM_BASE_URL"]
    assert out["fleet"] == "content" and out["default"] == "llm"
    assert out["robots"][0]["source"] == "fleet"


def test_a_supervisor_that_never_answered_says_so_rather_than_showing_nothing():
    """`None` is what the route hands over when the connection failed. The difference
    between "unreachable" and "no brains" is the whole point of the empty shape."""
    for payload in (None, {}, "", []):
        out = normalize_brain(payload)
        assert out["ok"] is False
        assert out["error"] == "supervisor not reachable"
        assert out["available"] == [] and out["robots"] == []


def test_a_refusal_keeps_its_sentence_so_the_card_can_show_it():
    """The supervisor's refusal names `MOXIE_APP` when an operator's pin is what blocked
    the pick. Losing that text would leave a parent with a button that does nothing."""
    refusal = {"ok": False,
               "error": f"'llm' cannot be chosen here. {brains.pin_note_for_pin('echo')}",
               "reason": "…"}
    out = normalize_brain(refusal)
    assert out["ok"] is False
    assert brains.ENV_VAR in out["error"]


def test_a_pinned_appliance_carries_its_note_beside_its_one_option():
    pinned = dict(LIVE, pin="echo", pin_note=brains.pin_note("echo"),
                  available=[e for e in LIVE["available"] if e["id"] == "echo"])
    out = normalize_brain(pinned)
    assert [e["id"] for e in out["available"]] == ["echo"]
    assert brains.ENV_VAR in out["pin_note"]


def test_a_truncated_or_hostile_payload_renders_instead_of_raising():
    """Every field arrives from another process; a card must never be a 500."""
    for payload in ({"ok": True, "available": "not-a-list", "robots": 7},
                    {"ok": True, "available": [None, 3, {"no": "id"}]},
                    {"ok": True, "installed": None},
                    {"ok": True, "robots": [None, "x"]}):
        out = normalize_brain(payload)
        assert isinstance(out["available"], list)
        assert isinstance(out["robots"], list)
        assert isinstance(out["installed"], list)


def test_a_field_a_newer_supervisor_invented_is_dropped_not_forwarded():
    """The console renders a fixed shape. A payload from a newer supervisor must not put
    unreviewed keys on a parent's page."""
    out = normalize_brain(dict(LIVE, surprise={"x": 1}))
    assert "surprise" not in out
    assert set(normalize_brain_option({"id": "llm", "extra": 1})) == {
        "id", "label", "group", "blurb", "needs", "default"}


def test_every_option_field_the_card_reads_is_always_present_and_typed():
    out = normalize_brain_option(None)
    assert out == {"id": "", "label": "", "group": "", "blurb": "", "needs": [],
                   "default": False}
    row = normalize_brain_robot(None)
    assert row["device_id"] == "" and row["source"] == "" and row["note"] == ""


def test_the_applied_report_of_a_write_comes_back_for_the_card_to_confirm():
    out = normalize_brain(dict(LIVE, applied={"scope": "robot", "device_id": "d_one",
                                              "brain": "echo"}))
    assert out["applied"]["brain"] == "echo"
    assert normalize_brain(dict(LIVE, applied="nope"))["applied"] is None


def test_the_shape_the_console_renders_covers_every_brain_the_registry_offers():
    """The card is fed by the appliance's registry, not by a list kept in the console —
    so a brain added to `brains.BRAINS` reaches the dropdown with no console change. This
    asserts the two really are the same set when the supervisor offers everything."""
    payload = dict(LIVE, available=[{"id": b, "label": brains.brain_label(b),
                                     "group": brains.BRAINS[b]["group"],
                                     "blurb": brains.BRAINS[b]["blurb"],
                                     "needs": list(brains.brain_needs(b))}
                                    for b in brains.BRAIN_IDS])
    out = normalize_brain(payload)
    assert [e["id"] for e in out["available"]] == list(brains.BRAIN_IDS)


# --------------------------------------------------- the card itself, structurally --
# There is no browser harness for `server/static/`, so the classic failure here is a
# silently dead card: the JS reaches for an id the HTML does not have (or the other way
# round) and nothing renders, with nothing failing. `test_console_roundtrip.py` keeps the
# same guards for its cards, but behind `importorskip("fastapi")` — CI's hermetic tier has
# none, so these read the shipped files off disk instead and always run.

def _asset(name):
    with open(os.path.join(REPO, "server", "static", name)) as fh:
        return fh.read()


def test_every_id_the_brain_card_drives_exists_in_the_page():
    html, js = _asset("index.html"), _asset("app.js")
    for element_id in ("brain-card", "brain-pick", "brain-scope", "brain-note",
                       "brain-robots", "brain-status", "btn-brain-save",
                       "btn-brain-clear", "btn-brain-refresh"):
        assert f'id="{element_id}"' in html, f"#{element_id} vanished from the page"
        assert f"'#{element_id}'" in js, f"#{element_id} is in the HTML but nothing drives it"


def test_the_card_is_refreshed_with_the_others_and_cleared_when_no_robot_is_live():
    """A card that is never called renders nothing, and a card that is never *cleared*
    keeps showing the last robot's brain after it goes offline."""
    js = _asset("app.js")
    assert "refreshBrain(liveDevice)" in js
    assert "refreshBrain(null)" in js


def test_the_card_reads_the_environments_pin_and_the_deciding_layer():
    """Two fields a parent cannot do without: the pin note is the reason the dropdown
    looks short, and `source` is the answer to "why is my child on that brain".
    Both would pass every API assertion above while never reaching the page."""
    js = _asset("app.js")
    assert "pin_note" in js, "the 🧠 card never reads the environment's pin"
    assert "BRAIN_SOURCE_TEXT" in js and "house rule" in js, \
        "the card never says which layer chose a robot's brain"


def test_the_console_route_forwards_the_scope_the_supervisor_expects():
    """`scope` travels as a query parameter (the supervisor's own route shape), and the
    card's own `scope` key must not be forwarded into the body as if it were a field."""
    with open(os.path.join(REPO, "server", "moxie_server", "main.py")) as fh:
        main = fh.read()
    assert '@app.get("/local/robots/{device_id}/brain")' in main
    assert '@app.post("/local/robots/{device_id}/brain")' in main
    assert "scope=fleet" in main
    assert 'k != "scope"' in main
