"""
🎭 Telehealth — the wire an operator drives the body with (audit ADOPT #7).

`mqtt/moxie_sdk/telehealth.py` is pure: builders for the recovered
`TelehealthRobotCommand`, a parser for the robot's state reports, and the closed
vocabulary a human picks from. Four things earn a test here and they are all in this file:

  * **the JSON keys are the recovered proto's, proven and not reviewed** — the highest
    value test in the file is `test_every_key_we_emit_is_a_recovered_field_name`, which
    reads `docs/reverse-engineering/protocol/recovered-proto/.../TeleHealth.proto` as the
    oracle (and cross-checks the compiled `TeleHealth_pb2` when protobuf is installed);
  * **the builder's refusals** — an unknown action, an empty `PLAY_OUTPUT`, and the two
    fields we deliberately never emit (`line_id` / `line_params`, assumption B5);
  * **the parser's honesty** — the four `RobotState` names, an unknown state kept verbatim
    and flagged rather than coerced, and a malformed payload that returns an empty view
    instead of raising on the MQTT loop;
  * **the vocabulary** — the 11 recovered moods, intensity 0-2 (not a 0.0-1.0 float),
    and the assumption constants that must not drift from `cloud_config.MoxieMode`.

Nothing here has been exercised against a physical robot; see
`docs/architecture/backlog/telehealth.md` §6 for the questions only one can settle.
"""
import os
import re
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk import telehealth as th                        # noqa: E402
from moxie_sdk import vocab                                   # noqa: E402
from moxie_sdk.cloud_config import MoxieMode                  # noqa: E402

RECOVERED_PROTO = os.path.join(
    REPO, "docs", "reverse-engineering", "protocol", "recovered-proto",
    "embodied", "telehealth", "TeleHealth.proto")


# --------------------------------------------------------------------------- #
# The recovered proto, parsed as the schema oracle (no protobuf needed)
# --------------------------------------------------------------------------- #
def _proto_text() -> str:
    with open(RECOVERED_PROTO) as fh:
        return fh.read()


def _proto_enum(name: str) -> list:
    """The member names of one recovered `enum`, in declaration order."""
    body = re.search(r"enum\s+%s\s*\{(.*?)\}" % name, _proto_text(), re.S).group(1)
    return re.findall(r"^\s*([A-Z_]+)\s*=\s*\d+;", body, re.M)


def _proto_fields(message: str) -> set:
    """The field names of one recovered `message`."""
    body = re.search(r"message\s+%s\s*\{(.*?)\n\}" % message, _proto_text(), re.S).group(1)
    return set(re.findall(r"\s([a-z_][a-z0-9_]*)\s*=\s*\d+;", body))


def test_the_recovered_proto_is_where_we_say_it_is():
    """The oracle is a committed file, not a memory — if it moves, this fails loudly."""
    assert os.path.isfile(RECOVERED_PROTO), RECOVERED_PROTO
    assert "package embodied.telehealth;" in _proto_text()


def test_our_enums_are_the_recovered_enums():
    assert list(th.ACTIONS) == _proto_enum("Action")
    assert list(th.STATES) == _proto_enum("RobotState")


# --------------------------------------------------------------------------- #
# T1 — build_telehealth_command
# --------------------------------------------------------------------------- #
def test_play_output_carries_text_and_markup_under_output():
    cmd = th.build_telehealth_command("PLAY_OUTPUT", text="Hello Sam",
                                      markup="<mark/>", session_id="ths-1",
                                      timestamp=1700000000000)
    assert cmd == {"command": "telehealth",
                   "message": {"timestamp": 1700000000000, "action": "PLAY_OUTPUT",
                               "output": {"text": "Hello Sam", "markup": "<mark/>"},
                               "session_id": "ths-1"}}


def test_markup_is_omitted_rather_than_sent_empty():
    msg = th.build_telehealth_command("PLAY_OUTPUT", text="hi")["message"]
    assert msg["output"] == {"text": "hi"}


@pytest.mark.parametrize("action", ["START_SESSION", "END_SESSION", "UPDATE_STATE",
                                    "INTERRUPT"])
def test_every_action_but_play_output_emits_no_output_key_at_all(action):
    """An empty `Output` on the wire would be a claim we have a line when we do not —
    and the acceptance criterion for INTERRUPT names this exactly."""
    msg = th.build_telehealth_command(action, session_id="ths-2")["message"]
    assert "output" not in msg
    assert msg["action"] == action
    assert msg["session_id"] == "ths-2"


def test_session_id_is_omitted_when_there_is_none():
    assert "session_id" not in th.build_telehealth_command("UPDATE_STATE")["message"]


def test_line_id_and_line_params_are_never_emitted():
    """ASSUMPTION B5: the field comment says "id of a pre-authored line" and we have no
    catalog of those ids. An id we cannot cite is an id we do not send."""
    cmd = th.build_telehealth_command("PLAY_OUTPUT", text="hi", markup="<m/>")
    assert set(cmd["message"]["output"]) == {"text", "markup"}


def test_an_unknown_action_raises():
    with pytest.raises(ValueError):
        th.build_telehealth_command("SPEAK")


def test_the_protos_zero_value_is_not_a_command():
    with pytest.raises(ValueError):
        th.build_telehealth_command("UNKNOWN_ACTION")


def test_play_output_with_no_text_raises():
    with pytest.raises(ValueError):
        th.build_telehealth_command("PLAY_OUTPUT", text="   ")


def test_the_action_name_is_case_insensitive_but_canonical_on_the_wire():
    assert th.build_telehealth_command("interrupt")["message"]["action"] == "INTERRUPT"


def test_the_timestamp_defaults_to_milliseconds():
    import time
    ts = th.build_telehealth_command("UPDATE_STATE")["message"]["timestamp"]
    assert abs(ts - time.time() * 1000) < 5000


def test_the_topic_is_the_recovered_command_topic():
    assert th.telehealth_topic("d_x") == "/devices/d_x/commands/telehealth"


def test_session_ids_are_unique():
    assert th.new_session_id() != th.new_session_id()
    assert th.new_session_id().startswith("ths-")


# --------------------------------------------------------------------------- #
# T3 — the recovered proto as the oracle
# --------------------------------------------------------------------------- #
def test_every_key_we_emit_is_a_recovered_field_name():
    """Every JSON key any builder can produce is a real field on the recovered message,
    and every action string a real `Action` member. A typo cannot ship."""
    command_fields = _proto_fields("TelehealthRobotCommand")
    message_fields = _proto_fields("TelehealthMessage")
    output_fields = _proto_fields("Output")
    seen_message, seen_output = set(), set()
    for action in th.ACTIONS:
        if action == "UNKNOWN_ACTION":
            continue
        kw = {"text": "hi", "markup": "<m/>"} if action == th.OUTPUT_ACTION else {}
        cmd = th.build_telehealth_command(action, session_id="ths-1", **kw)
        assert set(cmd) <= command_fields, set(cmd) - command_fields
        seen_message |= set(cmd["message"])
        seen_output |= set(cmd["message"].get("output") or {})
        assert cmd["message"]["action"] in _proto_enum("Action")
    assert seen_message <= message_fields, seen_message - message_fields
    assert seen_output <= output_fields, seen_output - output_fields
    # …and the two we refuse to emit really are fields, i.e. the omission is a decision.
    assert {"line_id", "line_params"} <= output_fields
    assert "line_id" not in seen_output and "line_params" not in seen_output


def test_the_compiled_proto_agrees_with_the_recovered_text():
    """The wire-tested `TeleHealth_pb2` (round-tripped in
    `tools/robot-toolkit/test_telehealth.py`) as a second opinion on the first. Skips
    where protobuf is absent — CI's hermetic env has none."""
    pytest.importorskip("google.protobuf", reason="the pb2 oracle needs protobuf")
    sys.path.insert(0, os.path.join(REPO, "tools", "robot-toolkit"))
    from embodied.telehealth import TeleHealth_pb2 as TH       # noqa: N814

    msg_fields = {f.name for f in TH.TelehealthMessage.DESCRIPTOR.fields}
    out_fields = {f.name for f in TH.Output.DESCRIPTOR.fields}
    cmd_fields = {f.name for f in TH.TelehealthRobotCommand.DESCRIPTOR.fields}
    assert msg_fields == _proto_fields("TelehealthMessage")
    assert out_fields == _proto_fields("Output")
    assert cmd_fields == _proto_fields("TelehealthRobotCommand")
    assert [v.name for v in TH.Action.DESCRIPTOR.values] == list(th.ACTIONS)
    assert [v.name for v in TH.RobotState.DESCRIPTOR.values] == list(th.STATES)


# --------------------------------------------------------------------------- #
# T2 — parse_telehealth_event
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("state", ["READY", "IN_SESSION", "EXITING", "UNKNOWN_STATE"])
def test_the_four_recovered_states_parse(state):
    got = th.parse_telehealth_event(
        {"subtopic": "telehealth",
         "message": {"state": state, "session_id": "ths-9", "timestamp": 1700000000000}})
    assert got["state"] == state and got["known"] is True
    assert got["session_id"] == "ths-9"
    assert got["at"] == 1700000000.0


def test_a_bare_message_parses_too():
    assert th.parse_telehealth_event({"state": "READY"})["state"] == "READY"


def test_a_numeric_state_resolves_to_its_recovered_name():
    assert th.parse_telehealth_event({"message": {"state": 2}})["state"] == "IN_SESSION"


def test_an_unknown_state_is_preserved_verbatim_and_flagged():
    """A robot telling us something new must not be silently rounded off to something we
    already believe."""
    got = th.parse_telehealth_event({"message": {"state": "CALIBRATING"}})
    assert got["state"] == "CALIBRATING"
    assert got["known"] is False


@pytest.mark.parametrize("payload", [None, "", [], {}, {"message": "nope"},
                                     {"message": {"timestamp": "soon"}}])
def test_a_malformed_payload_is_an_empty_view_not_an_exception(payload):
    got = th.parse_telehealth_event(payload)
    assert got["state"] == "" and got["at"] is None and got["known"] is False


def test_the_reported_action_is_kept_only_when_it_is_a_recovered_one():
    assert th.parse_telehealth_event(
        {"message": {"action": "UPDATE_STATE"}})["action"] == "UPDATE_STATE"
    assert th.parse_telehealth_event({"message": {"action": "WAVE"}})["action"] == ""


# --------------------------------------------------------------------------- #
# The vocabulary a human picks from
# --------------------------------------------------------------------------- #
def test_the_mood_list_is_the_eleven_recovered_moods_in_enum_order():
    names = [m["id"] for m in th.moods()]
    assert len(names) == 11
    assert names == [n for n, _ in sorted(vocab.MOODS.items(), key=lambda kv: kv[1])]
    assert names[0] == "neutral" and "shy" in names and "embarrassed" in names
    assert [m["value"] for m in th.moods()] == list(range(11))


def test_a_mood_alias_resolves_to_its_canonical_name():
    assert th.validate_mood("Joy") == "happy"
    assert th.validate_mood("happy") == "happy"
    assert th.validate_mood(8) == "confused"
    assert th.validate_mood(None) is None and th.validate_mood("") is None


def test_an_unknown_mood_is_refused_rather_than_dropped():
    """A picker is a closed vocabulary; a human deserves to be told their choice was not
    on it, instead of getting a neutral face and no explanation."""
    with pytest.raises(ValueError):
        th.validate_mood("sassy")
    with pytest.raises(ValueError):
        th.validate_mood(99)


def test_intensity_is_an_integer_0_to_2_and_clamps():
    assert vocab.MAX_INTENSITY == 2
    assert th.validate_intensity(0) == 0
    assert th.validate_intensity(2) == 2
    assert th.validate_intensity("1") == 1
    assert th.validate_intensity(7) == 2          # dragged past the end means "as strong"
    assert th.validate_intensity(-3) == 0
    assert th.validate_intensity(None) is None


def test_intensity_is_not_a_float_slider():
    """0-2, because `maxIntensity=2` is what the robot's own enum accepts — a 0.0-1.0
    float would silently collapse three steps into one."""
    assert th.validate_intensity(0.9) == 0
    with pytest.raises(ValueError):
        th.validate_intensity("loud")


def test_a_transcript_entry_is_text_only():
    entry = th.transcript_entry("operator", "hello", at=1700000000.0)
    assert entry == {"who": "operator", "text": "hello", "at": 1700000000.0}
    assert set(entry) == {"who", "text", "at"}       # no audio, no video, this phase
    assert th.transcript_entry("someone-else", "x")["who"] == "child"


# --------------------------------------------------------------------------- #
# ASSUMPTION B1 — one constant, and it must not drift
# --------------------------------------------------------------------------- #
def test_the_mode_constants_mirror_the_recovered_enum():
    """B1 lives behind `MOXIE_MODE_KEY`/`TELEHEALTH_MOXIE_MODE` exactly the way the
    unpaired status lives behind `UNPAIRED_PAIRING_STATUS` — so a capture that
    contradicts it is a one-line fix, and it can never disagree with `MoxieMode`."""
    assert th.TELEHEALTH_MOXIE_MODE == int(MoxieMode.TELEHEALTH) == 1
    assert th.DEFAULT_MOXIE_MODE == int(MoxieMode.DEFAULT_MODE) == 0
    assert th.MOXIE_MODE_KEY == "moxie_mode"


def test_the_fleet_layer_cannot_put_a_whole_household_into_puppet_mode():
    """`sanitize_config_overrides` is what the ⚙️ form (robot *and* fleet) posts through,
    and it does not whitelist `moxie_mode`. Puppet mode is a per-robot act."""
    from moxie_sdk.cloud_config import sanitize_config_overrides
    assert sanitize_config_overrides({"moxie_mode": 1, "audio_volume": 0.5}) == {
        "audio_volume": 0.5}


def test_the_subtopic_is_the_one_the_activity_log_multiplexes_on():
    assert th.EVENT_SUBTOPIC == "telehealth"
    doc = os.path.join(REPO, "docs", "architecture", "mqtt-and-conversation.md")
    with open(doc) as fh:
        assert 'subtopic:"telehealth"' in fh.read()
