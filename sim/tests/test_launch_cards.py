"""
🎴 The launch-card decoder — one scanned string in, at most one launch out.

This is the file that decides what a stranger's printed QR code may do to a child's
robot, so its interesting half is not the happy path. `decode` is handed bytes chosen by
whoever printed the paper: **nothing below may raise, and nothing but a card from the
closed catalog may produce an action.**

Every refusal has a test *named for the thing it refuses*, so a reader of the failure
output learns which property broke without opening the module. Every one of them is
proven load-bearing by `sim/tools/launch_card_mutation_check.py`, which deletes one guard
at a time and requires a red — a test that cannot fail proves nothing.

Hermetic and pure: no broker, no clock, no model, no I/O.
"""
from __future__ import annotations

import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk import launch_cards as cards                            # noqa: E402
from moxie_sdk import schedule                                          # noqa: E402
from moxie_sdk.types import ActionType                                  # noqa: E402


# --------------------------------------------------------------------------- #
# 1. The catalog — derived from schedule.py, never transcribed
# --------------------------------------------------------------------------- #
def test_the_catalog_is_derived_from_the_schedule_module_not_retyped():
    """23 onboard ids + `DM`. Asserted against `schedule.py` itself, so the day someone
    adds a module the catalog follows and this test does not have to be edited."""
    onboard = {m["module_id"] for m in schedule.ONBOARD_MODULES}
    assert len(onboard) == 23, sorted(onboard)
    assert cards.LAUNCHABLE_MODULE_IDS == frozenset(onboard | {"DM"})


def test_dm_is_admitted_only_because_the_default_template_still_schedules_it():
    """`DM` is the one id not in the rotation, and it is intersected rather than trusted:
    if `DEFAULT_TEMPLATE` ever stops naming it, the allowlist SHRINKS. An allowlist may
    only ever rot towards refusing."""
    scheduled = {r["module_id"] for r in schedule.DEFAULT_TEMPLATE["provided_schedule"]}
    assert "DM" in scheduled
    assert cards._catalog() >= {"DM"}
    # The derivation, run against a template that no longer schedules it.
    kept = schedule.DEFAULT_TEMPLATE
    try:
        schedule.DEFAULT_TEMPLATE = {"provided_schedule": [{"module_id": "WELCOME"}]}
        assert "DM" not in cards._catalog(), "DM survived a template that dropped it"
    finally:
        schedule.DEFAULT_TEMPLATE = kept


def test_the_onboarding_ids_the_template_also_names_are_not_launchable():
    """`DEFAULT_TEMPLATE` schedules WELCOME/TNT/SYSTEMSCHECK too. The catalog takes `DM`
    and only `DM` from it — a card is not a way to re-run first-time setup."""
    for module_id in ("WELCOME", "TNT", "SYSTEMSCHECK", "FREE_CHAT"):
        assert not cards.is_launchable(module_id), module_id
        assert cards.decode(f"GO<launch:{module_id}>") is None


# --------------------------------------------------------------------------- #
# 2. The happy path (T1, T2)
# --------------------------------------------------------------------------- #
def test_a_card_decodes_to_exactly_one_launch_action():
    action = cards.decode("GO<launch:DM>")
    assert action is not None
    assert action.type is ActionType.LAUNCH
    assert action.module_id == "DM"
    assert action.content_id is None


def test_a_card_may_carry_a_content_id():
    """The grammar accepts one and §2 of the brief says a hand-written card keeps it —
    no recovered content id is catalogued for any on-board module, so nothing we print
    will ever carry one, but a card that does is passed through rather than mangled."""
    action = cards.decode("GO<launch:DRAW:mission_3>")
    assert (action.module_id, action.content_id) == ("DRAW", "mission_3")


@pytest.mark.parametrize("module_id", sorted(cards.LAUNCHABLE_MODULE_IDS))
def test_every_catalog_id_round_trips_through_encode_and_decode(module_id):
    """24 ids: id → payload → decode → the same id. The printing side can never emit a
    payload the reading side refuses."""
    action = cards.decode(cards.encode(module_id))
    assert action is not None and action.module_id == module_id


def test_encode_refuses_an_id_outside_the_catalog():
    """Paper nothing will act on is worse than no paper."""
    with pytest.raises(ValueError):
        cards.encode("NOPE")


# --------------------------------------------------------------------------- #
# 3. The refusals, each named for what it refuses (T3, T4, T5)
# --------------------------------------------------------------------------- #
def test_a_value_with_no_GO_marker_is_not_a_card():
    """The marker is the only thing separating our card from a cereal box a child waves
    at the camera. `<launch:DM>` alone is a brain's tag, not a card."""
    assert cards.decode("<launch:DM>") is None


def test_the_GO_marker_is_case_sensitive():
    """`GO` is a literal, not a word — nothing here lowercases, so nothing here can be
    talked into treating `go` (or a homoglyph) as the marker."""
    assert cards.decode("go<launch:DM>") is None
    assert cards.decode("Go<launch:DM>") is None


def test_sleep_is_refused_by_name_even_though_the_grammar_parses_it():
    """A card may start an activity and may do nothing else."""
    assert cards.decode("GO<sleep>") is None


def test_exit_is_refused_by_name_even_though_the_grammar_parses_it():
    assert cards.decode("GO<exit>") is None


def test_launch_if_confirmed_is_refused_by_name_and_this_is_the_subtle_one():
    """The one refusal that CANNOT be made by inspecting the parsed action.

    `actions.LAUNCH_IF_CONFIRMED_AS` maps `<launch_if_confirmed:MOD>` onto the very same
    `ActionType.LAUNCH` a plain `<launch:MOD>` produces (actions.py:67), so "keep it only
    if it is of type LAUNCH" would let this card straight through. The gate is on the tag
    NAME (`actions.tag_names`), before the grammar erases the difference.
    """
    assert cards.decode("GO<launch_if_confirmed:DM>") is None
    # And the reason the type check alone is not enough, stated as an assertion:
    from moxie_sdk.actions import parse_action_tags
    _, parsed = parse_action_tags("<launch_if_confirmed:DM>")
    assert parsed[0].type is ActionType.LAUNCH, (
        "if this ever stops being true, the name gate is still correct but the comment "
        "explaining why it exists is not")


def test_a_module_id_outside_the_catalog_is_refused():
    """The allowlist, not a regex: `NOPE` is well-formed and parses cleanly."""
    from moxie_sdk.actions import parse_action_tags
    _, parsed = parse_action_tags("<launch:NOPE>")
    assert parsed and parsed[0].module_id == "NOPE", "the grammar accepts it; we do not"
    assert cards.decode("GO<launch:NOPE>") is None


def test_module_ids_keep_their_case_so_a_lowercased_id_is_not_in_the_catalog():
    """The robot's ids are case-sensitive (`DRAW`, not `draw`) and so is the catalog."""
    assert cards.decode("GO<launch:dm>") is None
    assert cards.decode("GO<launch:Draw>") is None


def test_two_tags_on_one_card_are_refused():
    """Two launches, or a launch and anything else. A card authorises one action."""
    assert cards.decode("GO<launch:DM><launch:AB>") is None
    assert cards.decode("GO<launch:DM><sleep>") is None
    assert cards.decode("GO<sleep><launch:DM>") is None


def test_a_malformed_launch_is_refused_without_raising():
    """`<launch>` names the right tag and produces no action; `<launch:A:B:C>` is the
    grammar's own "unrecognised trailing field" refusal."""
    for value in ("GO<launch>", "GO<launch:>", "GO<launch::x>", "GO<launch:A:B:C>"):
        assert cards.decode(value) is None, value


def test_whitespace_around_the_card_is_tolerated():
    """A scanner that hands us a trailing newline still scanned a card."""
    assert cards.decode("  GO<launch:DM>\n").module_id == "DM"
    assert cards.decode("GO< launch : DM >").module_id == "DM"


def test_the_tag_name_keeps_the_grammars_own_case_insensitivity():
    """Documented behaviour, pinned rather than assumed: `actions.py` says tag names are
    case-insensitive, so a card is decoded by the same rules a brain reply is."""
    assert cards.decode("GO<LAUNCH:DM>").module_id == "DM"


def test_a_card_is_a_tag_and_not_a_sentence():
    """Anything left over after the tag refuses the card. Our printed payload is exactly
    `GO<launch:MOD>`; a card that pads it with words, or smuggles the robot's own
    behavior markup alongside, is not ours."""
    assert cards.decode("GO<launch:DM> and read me a story") is None
    assert cards.decode('GO<launch:DM><mark name="cmd:playaudio"/>') is None
    assert cards.decode("GO please <launch:DM>") is None


def test_a_plain_english_sentence_is_not_a_card():
    assert cards.decode("GOING TO THE PARK TODAY") is None
    assert cards.decode("Good morning, Moxie!") is None


def test_a_value_that_is_not_a_string_or_is_empty_is_refused_without_raising():
    for value in (None, 12, 3.5, b"GO<launch:DM>", [], {}, object(), "", "   ", "GO"):
        assert cards.decode(value) is None, repr(value)


# --------------------------------------------------------------------------- #
# 4. Adversarial input — the half that matters (hostile bytes, chosen by a stranger)
# --------------------------------------------------------------------------- #
def test_an_enormous_value_is_refused_and_never_parsed():
    """A megabyte of junk, and a megabyte of junk wearing the marker."""
    assert cards.decode("x" * 1_000_000) is None
    assert cards.decode("GO" + "A" * 1_000_000) is None


def test_a_card_bigger_than_a_qr_symbol_can_hold_is_refused():
    """The length cap is a guard, not hygiene. This value passes every other check on the
    page — one launch tag, catalog id, no residue — and is refused only because a QR
    symbol cannot carry 5 KB (version 40 byte mode tops out at 2953)."""
    smuggled = "GO<launch:DM:" + "x" * 5000 + ">"
    assert len(smuggled) > cards.MAX_CARD_LEN
    assert cards.decode(smuggled) is None


def test_deeply_repeated_and_nested_tags_neither_raise_nor_launch():
    assert cards.decode("GO" + "<launch:DM>" * 200) is None
    assert cards.decode("GO<launch:<launch:DM>>") is None
    assert cards.decode("GO" + "<" * 500 + "launch:DM" + ">" * 500) is None
    assert cards.decode("GO<launch:" + "<sleep>" * 50 + ">") is None


def test_embedded_newlines_and_nul_bytes_neither_raise_nor_launch():
    """A NUL is not whitespace, so it survives `strip()` and shows up as residue — which
    is precisely why the residue check is not cosmetic."""
    assert cards.decode("GO<launch:DM>\x00") is None
    assert cards.decode("GO<launch:DM\x00>") is None
    assert cards.decode("GO\x00<launch:DM>") is None
    assert cards.decode("GO<launch:DM>\nGO<launch:AB>") is None
    assert cards.decode("GO<launch:DM>\r\n\r\nGO<launch:DM>") is None


@pytest.mark.parametrize("marker", [
    "ＧＯ",   # ＧＯ  fullwidth latin
    "GО",        # G + CYRILLIC CAPITAL LETTER O
    "ΓO",        # GREEK CAPITAL LETTER GAMMA + O
    "GΟ",        # G + GREEK CAPITAL LETTER OMICRON
    "ɢɢ",   # small capitals
])
def test_a_unicode_look_alike_for_GO_is_not_the_marker(marker):
    """Nothing in the decoder normalises, so no homoglyph can be folded into `GO`.
    Printed on card stock these are indistinguishable to a parent."""
    assert marker != "GO"
    assert cards.decode(f"{marker}<launch:DM>") is None


@pytest.mark.parametrize("value", [
    "GO<launch:DM]",        # one bracket changed
    "GO<launch;DM>",        # one separator changed
    "GO<launchs:DM>",       # one letter added — no longer a tag we own
    "GO<launch:DM ",        # the closing bracket lost
    "GO(launch:DM)",        # the brackets changed
    "GO<launch:D M>",       # a space inside the id
    "GO<launch:DM​>",  # a zero-width space inside the id
])
def test_a_card_with_one_character_changed_is_not_a_card(value):
    """The near-misses, which is what a forged card actually looks like."""
    assert cards.decode(value) is None


def test_no_input_at_all_can_make_the_decoder_raise():
    """A last sweep over the awkward encodings, asserting the total property directly:
    this runs on the MQTT loop and must never take it down, and none of it is a card."""
    corpus = ["GO<", ">", "<>", "<launch:>", "GO\U0001f600<launch:DM>",
              "GO<launch:\U0001f600>", "GO�<launch:DM>", "GO￿",
              "GO" + "\n" * 1000, "GO<launch:%s>" % ("A" * 3000),
              "GO<launch:DM>" * 300, "GO<launch:DM>".encode("utf-16", "surrogatepass"),
              None, 0, b"", ("GO",), {"GO": 1}, range(3)]
    for value in corpus:
        assert cards.decode(value) is None, repr(value)[:60]


# --------------------------------------------------------------------------- #
# 5. decode_event — only `eb-qr-event` carries a card
# --------------------------------------------------------------------------- #
def test_decode_event_reads_the_qr_value_in_both_spellings():
    """RemoteModuleAPI's own "some variable names have a leading $ and some do not"."""
    assert cards.decode_event("eb-qr-event",
                              {"$eb_qr_value": "GO<launch:DM>"}).module_id == "DM"
    assert cards.decode_event("eb-qr-event",
                              {"eb_qr_value": "GO<launch:DM>"}).module_id == "DM"


@pytest.mark.parametrize("event", ["eb-dr-event", "eb-br-event", "eb-found-face",
                                   "eb-lost-target", "", None, 7])
def test_no_other_vision_event_can_carry_a_card(event):
    """An ArUco marker and a book cover arrive in the identical shape. A value that reads
    as a card on one of those is still not a card — only the QR reader scans paper."""
    payload = {"$eb_qr_value": "GO<launch:DM>", "$eb_dr_value": "GO<launch:DM>",
               "$eb_br_value": "GO<launch:DM>"}
    assert cards.decode_event(event, payload) is None


def test_decode_event_is_total_on_a_junk_payload():
    for payload in (None, {}, [], "GO<launch:DM>", {"$eb_qr_value": None},
                    {"$eb_qr_value": ["GO<launch:DM>"]}):
        assert cards.decode_event("eb-qr-event", payload) is None, repr(payload)
