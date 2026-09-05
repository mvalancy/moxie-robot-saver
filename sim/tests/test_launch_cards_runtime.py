"""
🎴 Launch cards in the runtime — a scanned card reaching the reply the robot waits on.

The decoder's own behaviour lives in `test_launch_cards.py`. This file is about the one
call site: `_on_vision_turn`, the only place a QR value is in scope while a reply is
being built. Everything here drives real `events/remote-chat` payloads through the real
`MoxieRuntime` over a fake transport, the same way `test_presence_runtime.py` does.

**Honest ceiling.** No physical Moxie has ever sent us an `eb-qr-event`. Nothing in this
file proves a robot scans paper, or that a robot acts on the launch it is handed; it
proves that a scanned value which *did* arrive produces exactly the reply the recovered
contract describes, and that a value which is not a card produces none.

Hermetic: no sleeps, no broker, no model.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from helpers_runtime import drive_turn, make_runtime                    # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
from moxie_sdk import launch_cards as cards                             # noqa: E402
from moxie_sdk import presence as P                                     # noqa: E402
from moxie_sdk.app import MoxieApp                                      # noqa: E402
from moxie_sdk.types import Reply                                       # noqa: E402

QR, FOUND, LOST = P.QR_EVENT, P.FOUND_FACE, P.LOST_TARGET


class EchoApp(MoxieApp):
    name = "echo-cards"

    def __init__(self):
        self.turns = []

    def respond(self, turn):
        self.turns.append(turn)
        return Reply(text=f"You said: {turn.speech}")


def _runtime(app=None, *, greet_after_s=300.0, **kw):
    rt, dev = make_runtime(app or EchoApp(), **kw)
    rt.greet_after_s = greet_after_s
    return rt, dev


def _scan(rt, dev, value, *, event_id="evt-card", key="$eb_qr_value", event=QR):
    """Publish one QR vision event carrying `value`, and return the response."""
    return drive_turn(rt, dev, event, event_id=event_id, input_vars={key: value})


def _all_actions(resp):
    return (resp.get("output") or {}).get("actions") or resp.get("response_actions") or []


def _actions(resp):
    """The `RemoteChatAction`s that name a verb. Filtered, because a `SUCCESS` reply may
    also carry the runtime's vision `event_subscription` — an entry with no `action` at
    all — and that one is not something a card did."""
    return [a for a in _all_actions(resp) if a.get("action")]


def _seed_absent(rt, dev, away_s):
    """This robot went out of sight `away_s` ago — copied in spirit from
    `test_presence_runtime.py::_seed_absent`, clock-relative so it means the same thing
    at any hour."""
    now = time.time()
    rt.robots[dev].extra["presence"] = dict(
        P.new_state(), face_present=False, announced="left",
        last_seen_at=now - away_s - 30.0, present_since=now - away_s - 60.0,
        last_lost_at=now - away_s, absent_since=now - away_s, faces_seen=1, events=2)


# --------------------------------------------------------------------------- #
# T6 — a valid card produces exactly one launch, on this turn's own event_id
# --------------------------------------------------------------------------- #
def test_a_scanned_card_answers_with_exactly_one_launch_action():
    rt, dev = _runtime()
    resp = _scan(rt, dev, "GO<launch:DM>")
    assert resp["result"] == "SUCCESS", resp
    assert resp["event_id"] == "evt-card", resp
    acts = _actions(resp)
    assert len(acts) == 1, acts
    assert acts[0]["action"] == "launch" and acts[0]["module_id"] == "DM", acts


def test_a_card_carries_its_content_id_onto_the_wire():
    rt, dev = _runtime()
    acts = _actions(_scan(rt, dev, "GO<launch:DRAW:mission_3>"))
    assert acts[0]["module_id"] == "DRAW" and acts[0]["content_id"] == "mission_3", acts


def test_a_card_is_never_handed_to_a_brain_and_never_written_to_history():
    """The invariant `test_presence_runtime.py` pins for face events, held for cards: a
    perception event is not something a child said, so no brain call and no history."""
    app = EchoApp()
    rt, dev = _runtime(app)
    _scan(rt, dev, "GO<launch:DM>")
    assert app.turns == [], "a scanned card was handed to a brain as speech"
    assert rt.history.get(dev, []) == []


def test_a_card_alone_says_nothing_out_loud():
    """No invented spoken line: the reply carries the launch and stays silent, so a child
    never hears a decoding artefact. (`_maybe_synthesize` is likewise not reached — there
    is no text to speak.)"""
    rt, dev = _runtime()
    resp = _scan(rt, dev, "GO<launch:DM>")
    assert (resp["output"].get("text") or "") == "", resp
    tts = [t for (t, _) in rt.client.published if t.endswith("/commands/tts")]
    assert tts == [], tts


def test_the_scanned_value_still_reaches_the_presence_record():
    """P0-b reads the value; it does not re-model it. `presence.py` keeps working."""
    rt, dev = _runtime()
    _scan(rt, dev, "GO<launch:DM>")
    assert rt.robots[dev].extra["presence"]["qr"]["value"] == "GO<launch:DM>"


# --------------------------------------------------------------------------- #
# T7 — anything that is not a card is silence, never a stall and never an action
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [
    "not a card at all",
    "<launch:DM>",                    # no GO marker
    "GO<launch:NOPE>",                # outside the catalog
    "GO<sleep>",
    "GO<exit>",
    "GO<launch_if_confirmed:DM>",
    "GO<launch:DM><launch:AB>",
    "GO<launch:DM>\x00",
    "",
])
def test_a_value_that_is_not_a_card_answers_noreply_ack_with_no_action(value):
    """An unknown card is silence, never a stall: the contract requires *some* response
    ("the remote module must produce some response for this input to continue the
    interaction"), and `NOREPLY_ACK` is its own word for one that says nothing."""
    rt, dev = _runtime()
    resp = _scan(rt, dev, value)
    assert resp["result"] == "NOREPLY_ACK", (value, resp)
    assert _actions(resp) == [], (value, resp)


def test_a_refused_card_still_answers_the_turn_the_robot_is_waiting_on():
    rt, dev = _runtime()
    resp = _scan(rt, dev, "GO<launch:NOPE>", event_id="evt-refused")
    assert resp["command"] == "remote_chat" and resp["event_id"] == "evt-refused"


@pytest.mark.parametrize("event", [P.MARKER_EVENT, P.BOOK_EVENT, P.FOUND_FACE, P.LOST_TARGET])
def test_a_card_on_any_event_but_the_qr_one_launches_nothing(event):
    """`eb-dr-event` (an ArUco id) and `eb-br-event` (a book cover) arrive in the identical
    shape. Only the QR reader scans paper we printed.

    The payload deliberately carries **every** marker key at once — the hostile case. A
    route that looked at `input_vars` without checking which event it belongs to would
    find `$eb_qr_value` sitting right there and launch off a book cover."""
    rt, dev = _runtime()
    resp = drive_turn(rt, dev, event, event_id="evt-other",
                      input_vars={"$eb_qr_value": "GO<launch:DM>",
                                  "$eb_dr_value": "GO<launch:DM>",
                                  "$eb_br_value": "GO<launch:DM>"})
    assert _actions(resp) == [], (event, resp)


def test_the_grammar_trims_a_field_so_a_padded_id_is_still_that_id():
    """Documented tolerance, not a hole: `actions._fields` strips whitespace around each
    `:`-separated field, so `GO<launch:DM >` names `DM`. Pinned here because it looks like
    an allowlist bypass and is not one — the id the catalog is asked about is `DM`."""
    rt, dev = _runtime()
    acts = _actions(_scan(rt, dev, "GO<launch: DM >"))
    assert len(acts) == 1 and acts[0]["module_id"] == "DM", acts


def test_a_face_event_is_unchanged_by_this_slice():
    """The regression guard: the path that existed before still answers exactly as it
    did — no action, `NOREPLY_ACK`, nothing spoken."""
    rt, dev = _runtime()
    resp = drive_turn(rt, dev, FOUND, event_id="evt-face")
    assert resp["result"] == "NOREPLY_ACK" and _actions(resp) == []


# --------------------------------------------------------------------------- #
# T8 — a card and a hello are independent
# --------------------------------------------------------------------------- #
# A finding worth stating plainly, because the brief's T8 assumed otherwise: the two
# **cannot** co-occur in the field today. A greeting needs an `arrived` signal, and only
# `eb-found-face` produces one (`presence.update_presence` — a QR event yields a `qr`
# signal and touches neither `face_present` nor `present_since`). So a scan never earns a
# hello and a hello never carries a card, whatever the state of the robot. The runtime
# still composes the two on one reply rather than picking one, because that is the shape
# that stays correct if presence ever changes; the last test in this section pins that
# composition directly, since no wire input can reach it.
def test_a_qr_event_produces_no_arrival_signal_which_is_why_a_scan_never_greets():
    """The structural reason, asserted rather than assumed."""
    _, signals = P.update_presence(P.new_state(), QR, {"$eb_qr_value": "GO<launch:DM>"})
    assert [s["name"] for s in signals] == ["qr"], signals


def test_a_card_scanned_after_a_long_absence_launches_and_stays_silent():
    """Even a robot that has been away for ten minutes: the card launches, and the hello
    is not triggered, because a scan is not a sighting."""
    rt, dev = _runtime(greet_after_s=60.0)
    _seed_absent(rt, dev, away_s=600.0)
    resp = _scan(rt, dev, "GO<launch:DM>", event_id="evt-both")
    assert len(rt.client.chat_replies(dev)) == 1
    assert (resp["output"].get("text") or "") == "", resp
    assert len(_actions(resp)) == 1 and _actions(resp)[0]["module_id"] == "DM"


def test_the_greeting_still_fires_on_its_own_and_carries_no_launch():
    """`_greeting_for` is untouched by the card route — proven from the other side. One
    reply, spoken, with no verb on it (the vision `event_subscription` that rides an
    action-free reply is not a verb; see the next test)."""
    rt, dev = _runtime(greet_after_s=60.0)
    _seed_absent(rt, dev, away_s=600.0)
    resp = drive_turn(rt, dev, FOUND, event_id="evt-hello")
    assert len(rt.client.chat_replies(dev)) == 1
    assert resp["result"] == "SUCCESS" and resp["output"]["text"], resp
    assert _actions(resp) == [], "a face event must not carry a verb"


def test_a_card_reply_does_not_also_carry_the_vision_subscription():
    """Pre-existing rule, held: `_publish_chat` attaches `EventSubscription` only to a
    plain, action-free reply "so no reply that already carries a launch/exit changes
    shape". A card reply is now such a reply, and it must not become the exception."""
    rt, dev = _runtime()
    resp = _scan(rt, dev, "GO<launch:DM>")
    assert not any("event_subscription" in a for a in _all_actions(resp)), resp
    assert len(_all_actions(resp)) == 1, resp


def test_a_hello_and_a_card_on_one_turn_would_be_one_reply_carrying_both():
    """The composition, pinned white-box because no wire input can reach it (see the
    section note). If presence ever emits `arrived` alongside `qr`, this is the behaviour
    that must hold: ONE publish, the hello spoken, the launch attached — never two
    replies, never a doubled hello, never a hello that swallows the launch."""
    rt, dev = _runtime(greet_after_s=60.0)
    rt._greeting_for = lambda device_id, robot, signals: ("Hi again!", "<mark/>Hi again!")
    resp = _scan(rt, dev, "GO<launch:DM>", event_id="evt-both")
    assert len(rt.client.chat_replies(dev)) == 1
    assert resp["result"] == "SUCCESS" and resp["output"]["text"] == "Hi again!", resp
    assert len(_actions(resp)) == 1 and _actions(resp)[0]["module_id"] == "DM"


# --------------------------------------------------------------------------- #
# The catalog reaching the wire — the allowlist, end to end
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("module_id", sorted(cards.LAUNCHABLE_MODULE_IDS))
def test_every_catalog_id_reaches_the_wire_as_its_own_launch(module_id):
    rt, dev = _runtime()
    acts = _actions(_scan(rt, dev, cards.encode(module_id)))
    assert len(acts) == 1 and acts[0]["module_id"] == module_id, acts


@pytest.mark.parametrize("module_id", ["WELCOME", "TNT", "SYSTEMSCHECK", "FREE_CHAT",
                                       "../../etc/passwd", "*", "dm", "D M", "DM\u200b"])
def test_an_id_outside_the_catalog_never_reaches_the_wire(module_id):
    rt, dev = _runtime()
    resp = _scan(rt, dev, f"GO<launch:{module_id}>")
    assert _actions(resp) == [], (module_id, resp)
    assert resp["result"] == "NOREPLY_ACK", (module_id, resp)
