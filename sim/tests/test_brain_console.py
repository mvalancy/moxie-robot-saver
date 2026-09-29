"""🧠 The brain picker's console layer (`fleet.normalize_brain` + the route): a card is
never a 500 and never looks empty when the truth is "unreachable" — an empty dropdown
claims "this appliance has no brains"."""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "server"))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from helpers_console import console_js                                # noqa: E402
from helpers_console_supervisor import DEVICE, client, supervisor     # noqa: E402,F401
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


def test_the_console_route_forwards_scope_as_the_supervisors_query_not_the_body(client,
                                                                               supervisor):
    """The supervisor reads `scope` from the query; a body `scope` would be ignored and
    a house-rule pick would silently land on one robot."""
    r = client.post(f"/local/robots/{DEVICE}/brain", json={"brain": "echo", "scope": "fleet"})
    assert supervisor.brain_posts[-1] == ("scope=fleet", {"brain": "echo"})
    assert isinstance(r.json()["available"], list)
    client.post(f"/local/robots/{DEVICE}/brain", json={"brain": None})
    assert supervisor.brain_posts[-1] == (f"device_id={DEVICE}", {"brain": None})
    view = client.get(f"/local/robots/{DEVICE}/brain").json()
    assert view["ok"] is True and view["available"]


def test_every_id_the_brain_card_drives_exists_and_the_card_is_cleared_offline():
    """No browser suite loads this card: an id the HTML lost is a silently dead card, and
    a card never refreshed with `null` keeps showing the last robot's brain."""
    with open(os.path.join(REPO, "server", "static", "index.html")) as fh:
        html = fh.read()
    js = console_js()
    for element_id in ("brain-card", "brain-pick", "brain-scope", "brain-note",
                       "brain-robots", "brain-status", "btn-brain-save",
                       "btn-brain-clear", "btn-brain-refresh"):
        assert f'id="{element_id}"' in html and f"'#{element_id}'" in js, element_id
    assert "refreshBrain(liveDevice)" in js and "refreshBrain(null)" in js
