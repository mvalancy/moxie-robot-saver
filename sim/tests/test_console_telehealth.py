"""
The console's "Be Moxie" (puppet / telehealth) card against the supervisor's REAL runtime.

The one card where a mistake is audible in a child's room: the URL, the verb and the
body, and above all what the console does with the supervisor's 400 when the safety
classifier refuses a line — the operator is told why and the robot hears nothing. The
runtime behind `helpers_console_supervisor.FakeSupervisor` does the permit check, the
mode gate, the classifier and the publish.
"""
import json

import pytest

pytest.importorskip("fastapi", reason="console tests need fastapi")
pytest.importorskip("httpx", reason="console tests need httpx (fastapi TestClient)")

from helpers_console_supervisor import DEVICE, client, supervisor  # noqa: E402,F401

URL = f"/local/robots/{DEVICE}/telehealth"


def _act(client, action, **body):
    return client.post(URL, json={"action": action, **body})


@pytest.fixture()
def puppet(client, supervisor):
    """Puppet mode on and a session open, torn back down afterwards."""
    _act(client, "enable")
    _act(client, "start")
    supervisor.runtime.client.published.clear()
    yield
    _act(client, "disable")


def _wire(supervisor):
    return [p["message"] for p in supervisor.runtime.client.on(
        f"/devices/{DEVICE}/commands/telehealth")]


def test_the_card_reads_the_supervisors_telehealth_view(client, supervisor):
    r = client.get(URL)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["device_id"] == DEVICE
    assert len(body["moods"]) == 11 and body["max_intensity"] == 2
    # nothing has been reported by the robot, and the card must not invent a state
    assert body["reported"] is False and body["state"] == ""
    assert supervisor.telehealth_queries[-1] == DEVICE


def test_enable_speak_interrupt_disable_round_trips(client, supervisor, puppet):
    say = _act(client, "speak", text="Hello from Grandma.", mood="happy", intensity=2)
    assert say.status_code == 200
    body = say.json()
    assert body["ok"] is True and body["spoke"] == "Hello from Grandma."
    assert body["in_session"] is True
    cut = _act(client, "interrupt")
    assert cut.status_code == 200 and cut.json()["ok"] is True

    wire = _wire(supervisor)
    assert [m["action"] for m in wire] == ["PLAY_OUTPUT", "INTERRUPT"]
    assert wire[0]["output"]["text"] == "Hello from Grandma."
    assert "+mood+:1" in wire[0]["output"]["markup"]
    assert "+intensity+:2" in wire[0]["output"]["markup"]
    assert "output" not in wire[1]          # INTERRUPT carries no line


def test_the_operators_line_comes_back_in_the_transcript(client, puppet):
    _act(client, "speak", text="Time to brush your teeth.")
    body = client.get(URL).json()
    assert [(l["who"], l["text"]) for l in body["transcript"]][-1] == (
        "operator", "Time to brush your teeth.")


def test_a_line_the_safety_check_refuses_is_a_400_with_a_reason_and_is_never_spoken(
        client, supervisor, puppet):
    r = _act(client, "speak", text="you are a fucking idiot")
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False and body["blocked"] is True
    assert body["categories"] == ["profanity"]
    assert "Profanity" in body["reason"]
    assert _wire(supervisor) == []


def test_speaking_with_the_mode_off_is_a_400_the_card_can_act_on(client, supervisor):
    _act(client, "disable")
    supervisor.runtime.client.published.clear()
    r = _act(client, "speak", text="Hello.")
    assert r.status_code == 400 and "Be Moxie" in r.json()["reason"]
    assert _wire(supervisor) == []


def test_enable_re_pushes_the_config_with_the_mode_set(client, supervisor):
    supervisor.runtime.client.published.clear()
    for action, mode in (("enable", "TELEHEALTH"), ("disable", "DEFAULT_MODE")):
        _act(client, action)
        assert supervisor.runtime.client.on(
            f"/devices/{DEVICE}/config")[-1]["moxie_mode"] == mode


def test_the_robots_own_reported_state_reaches_the_card(client, supervisor, puppet):
    supervisor.runtime._on_activity(DEVICE, json.dumps(
        {"subtopic": "telehealth",
         "message": {"state": "IN_SESSION", "timestamp": 1700000000000}}))
    body = client.get(URL).json()
    assert body["state"] == "IN_SESSION" and body["state_known"] is True
    assert body["state_at"] == 1700000000.0


def test_an_unknown_verb_is_a_400(client):
    r = _act(client, "dance")
    assert r.status_code == 400 and r.json()["ok"] is False
