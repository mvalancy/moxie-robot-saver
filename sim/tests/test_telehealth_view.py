"""🎭 `fleet.normalize_telehealth`: the card never draws something the runtime did not
say — a never-reported state is not READY, an unknown state is flagged not coerced, and a
down supervisor / pending robot / safety refusal renders with its reason."""
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "server"))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_server.fleet import (normalize_telehealth,          # noqa: E402
                                normalize_transcript_line)

OK_VIEW = {
    "ok": True, "device_id": "d_1", "enabled": True, "online": True,
    "session_id": "ths-abc", "in_session": True,
    "state": "IN_SESSION", "state_at": 1700000000.0, "in_bedtime": False,
    "transcript": [{"who": "operator", "text": "Hello", "at": 1700000001.0},
                   {"who": "child", "text": "hi!", "at": 1700000002.0}],
    "moods": [{"id": "happy", "label": "Happy", "value": 1}],
    "max_intensity": 2,
}


def test_a_live_session_renders_whole():
    out = normalize_telehealth(OK_VIEW)
    assert out["ok"] is True and out["enabled"] is True and out["online"] is True
    assert out["session_id"] == "ths-abc" and out["in_session"] is True
    assert out["state"] == "IN_SESSION"
    assert out["reported"] is True and out["state_known"] is True
    assert out["state_at"] == 1700000000.0
    assert [l["who"] for l in out["transcript"]] == ["operator", "child"]
    assert out["moods"] == [{"id": "happy", "label": "Happy", "value": 1}]
    assert out["max_intensity"] == 2 and out["in_bedtime"] is False
    assert out["error"] is None
    # a quiet success carries no speak/refusal receipt keys
    assert "spoke" not in out and "flagged" not in out and "blocked" not in out


def test_a_state_the_robot_never_reported_is_not_invented():
    """The acceptance criterion: with no `subtopic:"telehealth"` event received, the card
    reads "never reported" — it does not assume READY."""
    out = normalize_telehealth(dict(OK_VIEW, state="", state_at=None))
    assert out["state"] == "" and out["reported"] is False
    assert out["state_known"] is False and out["state_at"] is None


def test_an_unknown_state_survives_the_trip_and_is_flagged():
    out = normalize_telehealth(dict(OK_VIEW, state="CALIBRATING"))
    assert out["state"] == "CALIBRATING"
    assert out["reported"] is True and out["state_known"] is False


def test_the_mode_being_off_still_renders_and_the_bedtime_warning_travels():
    out = normalize_telehealth(dict(OK_VIEW, enabled=False, in_session=False,
                                    session_id="", transcript=[], in_bedtime=True))
    assert out["ok"] is True and out["enabled"] is False and out["in_bedtime"] is True
    assert out["session_id"] == "" and out["transcript"] == []


@pytest.mark.parametrize("payload", [None, {}, [], "nope", 0])
def test_a_missing_or_junk_payload_is_a_renderable_empty_view(payload):
    out = normalize_telehealth(payload)
    assert out["ok"] is False and out["enabled"] is False
    assert out["transcript"] == [] and out["moods"] == []
    assert out["error"] == "supervisor not reachable"
    assert out["max_intensity"] == 2          # the card still has a working slider


def test_a_pending_robot_comes_back_with_the_reason_not_a_blank_card():
    out = normalize_telehealth({"ok": False, "device_id": "d_2",
                                "error": "not permitted",
                                "reason": "This robot is waiting to be permitted."})
    assert out["ok"] is False and out["error"] == "not permitted"
    assert out["reason"].startswith("This robot is waiting")


def test_a_safety_block_carries_its_categories_and_labels():
    out = normalize_telehealth({
        "ok": False, "device_id": "d_1", "error": "blocked", "blocked": True,
        "categories": ["profanity"], "labels": ["Profanity"],
        "reason": "Moxie will not say that (Profanity)."})
    assert out["blocked"] is True
    assert out["categories"] == ["profanity"] and out["labels"] == ["Profanity"]
    assert "Profanity" in out["reason"]


def test_a_successful_speak_confirms_what_was_said():
    out = normalize_telehealth(dict(OK_VIEW, spoke="Hello", flagged=["violence_talk"]))
    assert out["spoke"] == "Hello" and out["flagged"] == ["violence_talk"]


def test_a_transcript_line_is_text_only_and_never_trusts_who():
    assert normalize_transcript_line({"who": "operator", "text": "hi", "at": 1.0}) == {
        "who": "operator", "text": "hi", "at": 1.0}
    assert normalize_transcript_line({"who": "hacker", "text": "x"})["who"] == "child"
    assert set(normalize_transcript_line({})) == {"who", "text", "at"}


def test_junk_inside_the_transcript_and_moods_is_dropped_not_rendered():
    out = normalize_telehealth(dict(OK_VIEW, transcript=["nope", None, {"text": "ok"}],
                                    moods=["happy", {"id": "sad", "value": 2}]))
    assert [l["text"] for l in out["transcript"]] == ["ok"]
    assert out["moods"] == [{"id": "sad", "label": "", "value": 2}]


def test_it_is_pure_enough_for_the_hermetic_suite():
    """`fleet/` must import with fastapi refused (a subprocess import hook, since another
    test may already have imported fastapi into this session)."""
    import subprocess
    src = (
        "import sys\n"
        "WEB = ('fastapi', 'httpx', 'starlette', 'pydantic')\n"
        "class Refuse:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in WEB:\n"
        "            raise ImportError('refused: ' + name)\n"
        "sys.meta_path.insert(0, Refuse())\n"
        "try:\n"
        "    import fastapi\n"
        "    raise SystemExit('the import hook did not refuse fastapi')\n"
        "except ImportError:\n"
        "    pass\n"
        "sys.path.insert(0, %r)\n"
        "from moxie_server.fleet import normalize_telehealth\n"
        "print(normalize_telehealth({'ok': True})['max_intensity'])\n"
    ) % os.path.join(REPO, "server")
    out = subprocess.run([sys.executable, "-c", src], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "2"
