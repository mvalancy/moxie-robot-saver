"""
The presence helper — `mqtt/moxie_sdk/presence.py`.

Pure state machine, so every test here passes an explicit `now` and never sleeps. What
is pinned: the recovered payload keys (`$eb_qr_value` & friends), the arrived/left/flicker
signals, both hysteresis rules, the bounds, and the "an event we do not model can never
corrupt presence" guarantee.

Honest scope: no physical robot has ever sent us one of these events. These tests pin our
*model* of the recovered catalog (docs/architecture/vision.md §1.1-1.2), not observed
robot behavior.
"""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk import presence as P                          # noqa: E402

FOUND, LOST = P.FOUND_FACE, P.LOST_TARGET


def _names(signals):
    return [s["name"] for s in signals]


def _drive(script, state=None):
    """Play `[(t, event, payload?), …]`; return `(state, [signals per step])`."""
    st = state if state is not None else P.new_state()
    out = []
    for step in script:
        t, ev = step[0], step[1]
        payload = step[2] if len(step) > 2 else {}
        st, sigs = P.update_presence(st, ev, payload, t)
        out.append(sigs)
    return st, out


# --------------------------------------------------------------------------- #
# The vocabulary itself
# --------------------------------------------------------------------------- #
def test_the_recovered_event_names_are_the_ones_we_subscribe_to():
    """vision.md §1.1-1.2 / RemoteModuleAPI §Events — spelled exactly."""
    assert P.FOUND_FACE == "eb-found-face"
    assert P.LOST_TARGET == "eb-lost-target" and P.LOST_FACE == "eb-lost-face"
    assert (P.QR_EVENT, P.MARKER_EVENT, P.BOOK_EVENT) == (
        "eb-qr-event", "eb-dr-event", "eb-br-event")
    for name in ("eb-found-face", "eb-lost-target", "eb-qr-event",
                 "eb-dr-event", "eb-br-event"):
        assert name in P.VISION_EVENTS
    assert P.is_vision_event("eb-found-face") and P.is_vision_event(" eb-qr-event ")
    assert not P.is_vision_event("hello moxie")
    assert not P.is_vision_event(None) and not P.is_vision_event(7)


def test_the_close_enough_face_search_args_are_the_recovered_ones():
    """`eb_custom_face_search(min_width, min_height, …)` — floats as a proportion of the
    image view; 0.15 = "someone is close enough" (vision.md:40-45)."""
    assert P.CUSTOM_FACE_SEARCH == "eb_custom_face_search"
    assert P.BINNED_FACE_SEARCH == "eb_start_binned_face_search"
    assert P.CLOSE_ENOUGH_ARGS == ["0.15", "0", "0", "true", "true"]


# --------------------------------------------------------------------------- #
# arrived / left
# --------------------------------------------------------------------------- #
def test_the_first_face_is_an_arrival_with_no_away_time():
    st, sigs = P.update_presence(P.new_state(), FOUND, {}, 100.0)
    assert _names(sigs[-1] if isinstance(sigs, list) and sigs and isinstance(sigs[0], list)
                  else sigs) == ["arrived"]
    assert sigs[0]["away_s"] is None, "a first sighting has nothing to come back from"
    assert st["face_present"] is True
    assert st["faces_seen"] == 1 and st["present_since"] == 100.0


def test_unknown_until_the_robot_says_something():
    st = P.new_state()
    assert st["face_present"] is None, "None (never told) is not False (told they left)"
    assert P.snapshot(st, 0.0)["known"] is False


def test_leaving_after_a_real_presence_is_a_departure():
    st, sigs = _drive([(100.0, FOUND), (140.0, LOST)])
    assert _names(sigs[1]) == ["left"]
    assert sigs[1][0]["present_s"] == 40.0
    assert st["face_present"] is False and st["last_lost_at"] == 140.0


def test_coming_back_carries_how_long_they_were_gone():
    st, sigs = _drive([(100.0, FOUND), (140.0, LOST), (500.0, FOUND)])
    arrived = sigs[2][0]
    assert arrived["name"] == "arrived" and arrived["away_s"] == 360.0
    assert st["faces_seen"] == 2 and st["arrival_away_s"] == 360.0


def test_a_repeated_found_while_present_says_nothing():
    st, sigs = _drive([(100.0, FOUND), (105.0, FOUND), (110.0, FOUND)])
    assert sigs[1] == [] and sigs[2] == []
    assert st["faces_seen"] == 1
    assert st["present_since"] == 100.0, "a re-found must not restart the present clock"
    assert st["last_seen_at"] == 110.0


def test_a_repeated_lost_while_absent_says_nothing():
    st, sigs = _drive([(100.0, FOUND), (140.0, LOST), (150.0, LOST)])
    assert sigs[2] == []
    assert st["last_lost_at"] == 150.0


def test_the_lost_face_alias_is_the_same_event():
    """RemoteModuleAPI lists `eb-lost-face`; vision.md:48 calls it an alias of
    `eb-lost-target`. Both must end a presence."""
    st, sigs = _drive([(100.0, FOUND), (140.0, P.LOST_FACE)])
    assert _names(sigs[1]) == ["left"] and st["face_present"] is False


# --------------------------------------------------------------------------- #
# Hysteresis — the whole reason this is a state machine and not a boolean
# --------------------------------------------------------------------------- #
def test_a_face_that_blinks_out_and_back_is_a_flicker_not_a_return():
    """lost → found inside FLICKER_S: the same person, the tracker just blinked."""
    st, sigs = _drive([(100.0, FOUND), (140.0, LOST), (141.0, FOUND)])
    assert _names(sigs[2]) == ["flicker"]
    assert sigs[2][0]["direction"] == "found" and sigs[2][0]["gap_s"] == 1.0
    assert st["face_present"] is True
    assert st["faces_seen"] == 1, "a blink is not a new arrival"
    assert st["present_since"] == 100.0, "and it does not restart the present clock"
    assert st["flickers"] == 1


def test_a_one_frame_false_positive_does_not_read_as_walking_out():
    """found → lost inside MIN_PRESENT_S: never really a presence, so never a departure.
    The state still goes absent — the face IS gone."""
    st, sigs = _drive([(100.0, FOUND), (100.5, LOST)])
    assert _names(sigs[1]) == ["flicker"]
    assert sigs[1][0]["direction"] == "lost"
    assert st["face_present"] is False and st["flickers"] == 1


def test_a_burst_of_flicker_never_produces_a_second_arrival():
    script = [(100.0, FOUND)]
    t = 110.0
    for _ in range(20):                       # 20 blinks, 1 s apart
        script += [(t, LOST), (t + 1.0, FOUND)]
        t += 2.0
    st, sigs = _drive(script)
    flat = [s["name"] for step in sigs for s in step]
    assert flat.count("arrived") == 1, f"one person, one arrival — got {flat}"
    assert flat.count("left") == 1, f"one person, one departure — got {flat}"
    assert st["faces_seen"] == 1
    assert st["flickers"] == 39


def test_a_gap_past_the_flicker_window_is_a_real_return():
    st, sigs = _drive([(100.0, FOUND), (140.0, LOST), (140.0 + P.FLICKER_S + 0.5, FOUND)])
    assert _names(sigs[2]) == ["arrived"]
    assert st["faces_seen"] == 2


# --------------------------------------------------------------------------- #
# The semantic marker events (QR / ArUco / book)
# --------------------------------------------------------------------------- #
def test_qr_aruco_and_book_values_come_off_the_recovered_input_vars_keys():
    st, sigs = _drive([
        (10.0, P.QR_EVENT, {"$eb_qr_value": "GO<launch:DM>"}),
        (11.0, P.MARKER_EVENT, {"$eb_dr_value": "42"}),
        (12.0, P.BOOK_EVENT, {"$eb_br_value": "The Gruffalo"}),
    ])
    assert _names(sigs[0]) == ["qr"] and sigs[0][0]["value"] == "GO<launch:DM>"
    assert _names(sigs[1]) == ["marker"] and sigs[1][0]["value"] == "42"
    assert _names(sigs[2]) == ["book"] and sigs[2][0]["value"] == "The Gruffalo"
    assert st["qr"] == {"value": "GO<launch:DM>", "at": 10.0}
    assert st["book"]["value"] == "The Gruffalo"


def test_the_bare_spelling_of_a_value_key_is_accepted_too():
    """RemoteModuleAPI: "Some variable names have a leading $ and some do not"."""
    assert P.value_of({"eb_qr_value": "GO"}, P.QR_EVENT) == "GO"
    assert P.value_of({"$eb_qr_value": "GO"}, P.QR_EVENT) == "GO"


def test_a_marker_event_does_not_disturb_presence():
    st, _ = _drive([(100.0, FOUND), (110.0, P.QR_EVENT, {"$eb_qr_value": "GO"})])
    assert st["face_present"] is True and st["present_since"] == 100.0


# --------------------------------------------------------------------------- #
# Robustness: garbage in must never corrupt presence
# --------------------------------------------------------------------------- #
def test_an_event_we_do_not_model_is_a_no_op():
    st, sigs = P.update_presence(P.new_state(), "eb-wait-complete", {}, 5.0)
    assert sigs == [] and st == P.new_state()
    st2, sigs2 = P.update_presence(st, "", None, 6.0)
    assert sigs2 == [] and st2["events"] == 0


def test_a_missing_or_wrong_shaped_payload_never_raises():
    for payload in (None, "", 7, [], {"nope": 1}):
        st, sigs = P.update_presence(P.new_state(), P.QR_EVENT, payload, 1.0)
        assert sigs[0]["value"] == ""
        assert st["qr"]["value"] == ""
    st, sigs = P.update_presence(None, FOUND, None, 1.0)     # no prior state at all
    assert _names(sigs) == ["arrived"]


def test_a_partial_or_legacy_record_is_healed_not_rejected():
    st, sigs = P.update_presence({"face_present": True}, LOST, {}, 50.0)
    assert st["last_lost_at"] == 50.0 and st["face_present"] is False
    assert "faces_seen" in st and "history" in st


def test_a_clock_that_steps_backwards_cannot_invent_a_duration():
    st, sigs = _drive([(500.0, FOUND), (400.0, LOST)])
    assert sigs[1][0]["gap_s"] == 0.0 or sigs[1][0].get("present_s") == 0.0


def test_update_presence_never_mutates_the_state_it_was_given():
    before = P.new_state()
    snapshot_of_before = dict(before)
    after, _ = P.update_presence(before, FOUND, {}, 1.0)
    assert before == snapshot_of_before, "the input state was mutated"
    assert after is not before and after["history"] is not before["history"]


def test_the_event_history_is_bounded():
    script = [(float(i), FOUND if i % 2 else LOST) for i in range(200)]
    st, _ = _drive(script)
    assert len(st["history"]) == P.HISTORY_MAX
    assert st["events"] == 200, "the counter still counts everything"
    assert st["history"][-1]["at"] == 199.0


# --------------------------------------------------------------------------- #
# The Turn snapshot + the prompt line
# --------------------------------------------------------------------------- #
def test_the_snapshot_is_json_safe_durations_not_timestamps():
    st, _ = _drive([(100.0, FOUND)])
    snap = P.snapshot(st, 160.0)
    import json
    json.dumps(snap)                                  # must not raise
    assert snap["known"] is True and snap["face_present"] is True
    assert snap["present_s"] == 60.0 and snap["away_s"] is None
    assert snap["since_seen_s"] == 60.0 and snap["faces_seen"] == 1


def test_the_prompt_line_is_empty_for_a_settled_conversation():
    """Most turns say nothing — a standing "a child is visible" would be a per-turn tax
    on the context window."""
    st, _ = _drive([(100.0, FOUND)])
    assert P.prompt_line(st, 100.0 + P.JUST_ARRIVED_S + 1) == ""
    assert P.snapshot(st, 400.0)["line"] == ""


def test_the_prompt_line_says_someone_just_came_back_and_for_how_long_they_were_gone():
    st, _ = _drive([(100.0, FOUND), (140.0, LOST), (1340.0, FOUND)])
    line = P.prompt_line(st, 1341.0)
    assert "just came back" in line and "20 minutes" in line
    assert "camera" not in line.lower(), "keep it about people, not hardware"


def test_the_prompt_line_says_when_the_room_has_been_empty():
    st, _ = _drive([(100.0, FOUND), (140.0, LOST)])
    assert P.prompt_line(st, 150.0) == "", "a short absence is not worth saying"
    line = P.prompt_line(st, 140.0 + P.LONG_ABSENCE_S + 1)
    assert "Nobody has been visible" in line


def test_the_prompt_line_is_empty_when_vision_has_told_us_nothing():
    assert P.prompt_line(P.new_state(), 10.0) == ""
    assert P.prompt_line({}, 10.0) == "" and P.prompt_line(None, 10.0) == ""


def test_durations_are_spoken_vaguely_never_as_raw_seconds():
    assert P.human_duration(3) == "a few seconds"
    assert P.human_duration(60) == "about a minute"
    assert P.human_duration(600) == "about 10 minutes"
    assert P.human_duration(3700) == "about an hour"
    assert P.human_duration(7200) == "about 2 hours"
    assert P.human_duration("nonsense") == "a moment"


# --------------------------------------------------------------------------- #
# The greeting lines themselves
# --------------------------------------------------------------------------- #
def test_a_greeting_is_short_warm_and_uses_the_child_s_name():
    for _ in range(30):
        line = P.pick_greeting("Sam")
        assert "Sam" in line
        assert len(line) < 70, line
        assert "{" not in line and "}" not in line


def test_a_greeting_is_never_the_same_one_twice_running():
    last = P.pick_greeting("Sam")
    for _ in range(50):
        nxt = P.pick_greeting("Sam", last)
        assert nxt != last
        last = nxt


def test_a_missing_nickname_still_produces_a_line():
    assert "friend" in P.pick_greeting("")
    assert "friend" in P.pick_greeting(None)
