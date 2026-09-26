"""
The console's Speech and Listening pickers against the supervisor's REAL voice verbs.

The console never keeps a list of voices: it renders what the supervisor says this
appliance can genuinely use. This is that seam — the URL the card builds, the body it
posts, and the 400 a stale page earns. Validation, persistence and the engine swap are
the runtime's own; only the engine builders are faked (`helpers_console_supervisor`).
Every test that picks a voice resets the pick afterwards.
"""
import pytest

pytest.importorskip("fastapi", reason="console tests need fastapi")
pytest.importorskip("httpx", reason="console tests need httpx (fastapi TestClient)")

from helpers_console_supervisor import DEVICE, client, supervisor  # noqa: E402,F401

URL = f"/local/robots/{DEVICE}/voice"


@pytest.fixture()
def reset_pick(client):
    yield
    client.post(URL, json={"speech": None})


def test_the_voice_card_gets_every_option_grouped_for_its_dropdowns(client, supervisor):
    before = len(supervisor.voice_queries)
    r = client.get(URL)
    assert r.status_code == 200, r.text
    v = r.json()
    assert supervisor.voice_queries[before:] == [""]
    assert v["ok"] is True and v["error"] is None
    speech = [e["id"] for e in v["available"]["speech"]]
    assert "gateway:piper-amy" in speech and "piper:en_US-amy-medium" in speech
    assert speech[-1] == "tone"
    assert [e["id"] for e in v["available"]["listening"]][-1] == "off"
    assert {e["group"] for e in v["available"]["speech"]} == {"Gateway", "Local",
                                                              "Built-in"}
    # chat models never leak into a voice picker
    assert not any("graphling-medium" in e for e in speech)


def test_the_default_is_piper_amy_and_it_is_marked_for_the_card(client):
    v = client.get(URL).json()
    assert v["selected"]["speech"] == "gateway:piper-amy"
    assert v["selected"]["listening"] == "gateway:stt-whisper"
    assert [e["id"] for e in v["available"]["speech"] if e["default"]] == \
        ["gateway:piper-amy"]
    assert v["chosen"] == {"speech": False, "listening": False}


def test_a_refresh_is_forwarded_to_the_supervisor(client, supervisor):
    before = len(supervisor.voice_queries)
    client.get(f"{URL}?refresh=true")
    assert supervisor.voice_queries[before:] == ["1"]


def test_picking_a_voice_round_trips_and_sticks(client, supervisor, reset_pick):
    r = client.post(URL, json={"speech": "gateway:piper-ryan"})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["ok"] is True and v["selected"]["speech"] == "gateway:piper-ryan"
    assert supervisor.voice_posts[-1] == ("/voice", {"speech": "gateway:piper-ryan"})
    # the next poll agrees, because it was persisted, not held in the page
    assert client.get(URL).json()["selected"]["speech"] == "gateway:piper-ryan"
    # the engine actually installed is the one that was picked
    assert supervisor.runtime._synth.choice["model"] == "piper-ryan"


def test_a_local_pick_is_honoured_with_a_gateway_configured(client, supervisor, reset_pick):
    r = client.post(URL, json={"speech": "piper:en_US-amy-medium"})
    assert r.status_code == 200
    assert r.json()["selected"]["speech"] == "piper:en_US-amy-medium"
    assert supervisor.runtime._synth.choice["engine"] == "piper"


def test_a_stale_page_gets_a_400_with_the_reason_not_a_silent_no_op(client):
    r = client.post(URL, json={"speech": "gateway:piper-bob"})
    assert r.status_code == 400, r.text
    v = r.json()
    assert v["ok"] is False and "piper-bob" in (v["reason"] or "")
    assert "gateway:piper-amy" in v["reason"], "the refusal must say what IS available"


def test_a_pinned_engine_shortens_the_dropdown_and_says_which_variable_did_it(
        client, supervisor, reset_pick):
    """`MOXIE_TTS=piper` is an owner rule. The card must not offer the gateway voices it
    forbids, and a stale page posting one gets the variable's name back — not a bare
    refusal that reads as a gateway that lost half its voices."""
    engines = supervisor.runtime._voice_engines
    engines.pins = {"speech": "piper", "listening": ""}
    try:
        v = client.get(URL).json()
        assert [e["id"] for e in v["available"]["speech"]] == ["piper:en_US-amy-medium"]
        assert "MOXIE_TTS=piper" in v["pin_notes"]["speech"]
        assert v["pins"]["speech"] == "piper"
        # the ears are unpinned, so nothing about them changes
        assert "gateway:stt-whisper" in [e["id"] for e in v["available"]["listening"]]
        assert v["pin_notes"]["listening"] == ""
        r = client.post(URL, json={"speech": "gateway:piper-amy"})
        assert r.status_code == 400, r.text
        assert "MOXIE_TTS=piper" in (r.json()["reason"] or "")
        # …and a pick INSIDE the pinned engine still works, so the card is not dead
        ok = client.post(URL, json={"speech": "piper:en_US-amy-medium"})
        assert ok.status_code == 200
        assert ok.json()["selected"]["speech"] == "piper:en_US-amy-medium"
    finally:
        engines.pins = {}


def test_the_test_button_plays_a_line_on_the_named_robot(client, supervisor, reset_pick):
    client.post(URL, json={"speech": "gateway:piper-amy"})
    topic = f"/devices/{DEVICE}/commands/tts"
    before = len(supervisor.runtime.client.on(topic))
    r = client.post(f"{URL}/test", json={"text": "Hello from the console."})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["spoke"] == "Hello from the console."
    published = supervisor.runtime.client.on(topic)
    assert len(published) == before + 1
    assert published[-1]["audio"]["sample_rate"] == 22050
    assert published[-1]["audio"]["buffer"], "the Test button published no audio"


def test_testing_a_robot_that_is_not_connected_is_a_404(client):
    r = client.post("/local/robots/d_nobody/voice/test", json={})
    assert r.status_code == 404, r.text
    assert r.json()["ok"] is False
