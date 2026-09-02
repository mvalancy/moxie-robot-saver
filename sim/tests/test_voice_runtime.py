"""
🎚️ The voice picker's live half — the runtime region, the three status-HTTP routes and
the swap that a turn actually feels.

`test_voice_settings.py` covers the pure module and the builders' `override=`. This file
covers what a parent's click does to a running supervisor: the card's data, the record on
disk, the engine that speaks the NEXT turn, and the Test button's real `CloudTTSResponse`.

Hermetic: the appliance's engine builders arrive through `set_voice_engines()` — the seam
the runtime was given precisely so no test needs `openai`, `piper`, `faster-whisper` or a
gateway. The HTTP tier goes through `MoxieRuntime._start_status_server` itself (via
`helpers_runtime.status_server`), so it proves the real handlers rather than a double.
"""
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MQTT = os.path.join(REPO, "mqtt")
for _p in (MQTT, os.path.join(MQTT, "supervisor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers_runtime import (drive_turn, http_json, make_runtime,  # noqa: E402
                             status_server)
from moxie_sdk import voice_settings as vs                          # noqa: E402
from moxie_sdk.apps import EchoApp                                  # noqa: E402
from moxie_sdk.store import JsonStore                               # noqa: E402
from moxie_sdk.stt import Transcriber                               # noqa: E402
from moxie_sdk.tts import Synthesizer                               # noqa: E402

#: The gateway's real 2026-09-02 listing (see test_voice_settings.py).
GATEWAY_MODELS = [
    "piper-amy", "piper-ryan", "graphling-tts-narrator", "graphling-tts-character",
    "stt-whisper", "graphling-stt", "tts-piper-amy", "tts-piper-ryan",
    "stt-whisper-base", "graphling-medium", "qwen2.5-7b",
]
TTS_TOPIC = "/devices/{device_id}/commands/tts"


class _Voice(Synthesizer):
    """A synthesizer that remembers which choice built it and what it was asked to say."""
    channels = 1

    def __init__(self, choice, sample_rate=22050):
        self.choice = dict(choice)
        self.sample_rate = sample_rate
        self.spoken = []

    def describe(self):
        return f"fake-voice ({vs.choice_id(self.choice)})"

    def synthesize(self, text, voice=None):
        self.spoken.append(text)
        return b"\x11\x22" * 64


class _Ears(Transcriber):
    def __init__(self, choice):
        self.choice = dict(choice)

    def describe(self):
        return f"fake-ears ({vs.choice_id(self.choice)})"

    def transcribe(self, pcm, sample_rate=16000):
        return "heard"


class _Engines:
    """Stands in for `config.VoiceEngines`: scripted availability, recorder builders.

    `fail` names sides whose build must return None — the "this pick cannot be built on
    this box" case, which must never cost the appliance the engine it already had.
    """

    def __init__(self, *, gateway=None, piper=(), whisper=(), error="",
                 discovering=False, fail=()):
        self.gateway = list(GATEWAY_MODELS if gateway is None else gateway)
        self.piper, self.whisper = list(piper), list(whisper)
        self.error, self.discovering = error, discovering
        self.fail = set(fail)
        self.asked = []                  # every choice a builder was handed
        self.refreshes = 0
        self.settles = []                # the settle budget each caller asked for

    def available(self, *, refresh=False, settle_s=0.0):
        self.refreshes += int(bool(refresh))
        self.settles.append(settle_s)
        return {"available": vs.build_available(self.gateway,
                                                piper_voices=self.piper,
                                                whisper_models=self.whisper),
                "discovering": self.discovering, "gateway_error": self.error}

    def build_speech(self, choice):
        self.asked.append((vs.SPEECH, dict(choice)))
        if vs.SPEECH in self.fail:
            return None
        return None if choice["engine"] == "off" else _Voice(choice)

    def build_listening(self, choice):
        self.asked.append((vs.LISTENING, dict(choice)))
        if vs.LISTENING in self.fail or choice["engine"] == "off":
            return None
        return _Ears(choice)


def _runtime(tmp_path, engines=None, **kw):
    rt, device_id = make_runtime(EchoApp(), store=JsonStore(str(tmp_path)), **kw)
    if engines is not None:
        rt.set_voice_engines(engines)
    return rt, device_id


# ----------------------------------------------------------------- the card --
def test_the_card_lists_the_gateway_the_local_engines_and_the_builtins(tmp_path):
    rt, _ = _runtime(tmp_path, _Engines(piper=["en_US-amy-medium"], whisper=["base.en"]))
    view = rt.voice_view()
    assert view["ok"] is True
    assert vs.option_ids(view["available"][vs.SPEECH]) == [
        "gateway:piper-amy", "gateway:piper-ryan", "gateway:graphling-tts-narrator",
        "gateway:graphling-tts-character", "gateway:tts-piper-amy",
        "gateway:tts-piper-ryan", "piper:en_US-amy-medium", "tone"]
    assert vs.option_ids(view["available"][vs.LISTENING]) == [
        "gateway:stt-whisper", "gateway:graphling-stt", "gateway:stt-whisper-base",
        "whisper:base.en", "off"]


def test_acceptance_1_the_defaults_are_piper_amy_and_stt_whisper(tmp_path):
    rt, _ = _runtime(tmp_path, _Engines())
    view = rt.voice_view()
    assert view["selected"] == {vs.SPEECH: "gateway:piper-amy",
                                vs.LISTENING: "gateway:stt-whisper"}
    assert view["labels"][vs.SPEECH] == "Amy (gateway, piper-amy)"
    assert view["chosen"] == {vs.SPEECH: False, vs.LISTENING: False}
    marked = [e["id"] for e in view["available"][vs.SPEECH] if e["default"]]
    assert marked == ["gateway:piper-amy"]


def test_acceptance_3_with_no_gateway_the_card_shows_local_and_builtin_only(tmp_path):
    rt, _ = _runtime(tmp_path, _Engines(gateway=[], piper=["en_US-amy-medium"],
                                        whisper=["base.en"]))
    view = rt.voice_view()
    assert view["selected"] == {vs.SPEECH: "piper:en_US-amy-medium",
                                vs.LISTENING: "whisper:base.en"}


def test_acceptance_5_a_gateway_outage_never_blanks_the_card(tmp_path):
    rt, _ = _runtime(tmp_path, _Engines(gateway=[], piper=["en_US-amy-medium"],
                                        error="APIConnectionError"))
    view = rt.voice_view()
    assert view["ok"] is True and view["gateway_error"] == "APIConnectionError"
    assert vs.option_ids(view["available"][vs.SPEECH]) == ["piper:en_US-amy-medium",
                                                           "tone"]


def test_discovery_still_running_is_said_out_loud(tmp_path):
    rt, _ = _runtime(tmp_path, _Engines(gateway=[], discovering=True))
    assert rt.voice_view()["discovering"] is True


def test_an_engine_adapter_that_throws_degrades_to_the_builtins(tmp_path):
    class _Broken:
        def available(self, *, refresh=False, settle_s=0.0):
            raise RuntimeError("the adapter itself blew up")

    rt, _ = _runtime(tmp_path, _Broken())
    view = rt.voice_view()
    assert view["ok"] is True and view["gateway_error"] == "RuntimeError"
    assert vs.option_ids(view["available"][vs.SPEECH]) == ["tone"]


def test_without_any_engines_the_card_still_offers_the_builtins(tmp_path):
    """The runtime never imports `config`; with nothing injected it says so honestly
    rather than claiming models it could not build."""
    rt, _ = _runtime(tmp_path)
    view = rt.voice_view()
    assert view["selected"] == {vs.SPEECH: "tone", vs.LISTENING: "off"}


def test_the_card_reports_the_engine_that_is_actually_installed(tmp_path):
    rt, _ = _runtime(tmp_path, _Engines())
    rt.set_synthesizer(_Voice(vs.make_choice("gateway", "piper-amy")))
    assert rt.voice_view()["installed"][vs.SPEECH] == "fake-voice (gateway:piper-amy)"
    assert rt.voice_view()["installed"][vs.LISTENING] == ""


def test_a_refresh_is_passed_through_to_discovery(tmp_path):
    engines = _Engines()
    rt, _ = _runtime(tmp_path, engines)
    rt.voice_view()
    assert engines.refreshes == 0
    rt.voice_view(refresh=True)
    assert engines.refreshes == 1


# --------------------------------------------------------------- the choice --
def test_a_pick_is_persisted_and_installed(tmp_path):
    engines = _Engines()
    rt, _ = _runtime(tmp_path, engines)
    out = rt.voice_update({"speech": "gateway:piper-ryan"})
    assert out["ok"] is True
    assert out["selected"][vs.SPEECH] == "gateway:piper-ryan"
    assert rt._synth.choice == {"engine": "gateway", "model": "piper-ryan"}
    assert out["applied"][vs.SPEECH]["line"] == "speech: piper-ryan (gateway, chosen)"
    # …and it is on disk, in the fleet record, for the next boot
    assert vs.read_settings(rt.store)["speech"] == {"engine": "gateway",
                                                    "model": "piper-ryan"}
    assert os.path.isfile(os.path.join(str(tmp_path), "fleet", "voice.json"))


def test_acceptance_2_the_next_turn_uses_the_new_voice(tmp_path):
    """No restart: the turn after the swap is synthesized by the engine just chosen, and
    the audio goes out on the robot's own `commands/tts`."""
    rt, device_id = _runtime(tmp_path, _Engines())
    rt.voice_update({"speech": "gateway:piper-ryan"})
    drive_turn(rt, device_id, "hello moxie")
    assert rt._synth.spoken, "the picked voice never spoke"
    tts = rt.client.on(TTS_TOPIC.format(device_id=device_id))
    assert tts and tts[-1]["audio"]["sample_rate"] == 22050


def test_acceptance_2_a_restart_comes_back_with_the_same_choice(tmp_path):
    """The record is fleet-level and on disk, so a fresh supervisor over the same data
    dir resolves to the pick rather than to the default."""
    rt, _ = _runtime(tmp_path, _Engines())
    rt.voice_update({"speech": "gateway:piper-ryan", "listening": "gateway:graphling-stt"})
    reborn, _ = _runtime(tmp_path, _Engines(), device_id="d_again")
    view = reborn.voice_view()
    assert view["selected"] == {vs.SPEECH: "gateway:piper-ryan",
                                vs.LISTENING: "gateway:graphling-stt"}
    assert view["chosen"] == {vs.SPEECH: True, vs.LISTENING: True}


def test_acceptance_4_an_explicit_local_pick_wins_with_a_gateway_configured(tmp_path):
    engines = _Engines(piper=["en_US-amy-medium"], whisper=["base.en"])
    rt, _ = _runtime(tmp_path, engines)
    rt.voice_update({"speech": "piper:en_US-amy-medium", "listening": "whisper:base.en"})
    assert rt._synth.choice == {"engine": "piper", "model": "en_US-amy-medium"}
    assert rt._transcriber.choice == {"engine": "whisper", "model": "base.en"}
    # the gateway was never asked to build anything for this appliance
    assert all(c["engine"] != "gateway" for _, c in engines.asked)


def test_picking_off_really_silences_the_ears(tmp_path):
    rt, _ = _runtime(tmp_path, _Engines())
    rt.voice_update({"listening": "off"})
    assert rt._transcriber is None
    assert rt.voice_view()["selected"][vs.LISTENING] == "off"


def test_an_unlisted_pick_is_refused_and_changes_nothing(tmp_path):
    rt, _ = _runtime(tmp_path, _Engines())
    rt.voice_update({"speech": "gateway:piper-ryan"})
    before = rt._synth
    out = rt.voice_update({"speech": "gateway:piper-bob"})
    assert out["ok"] is False and "piper-bob" in out["reason"]
    assert "gateway:piper-amy" in out["reason"], "the refusal must say what IS available"
    assert rt._synth is before, "a refused pick must not disturb the live engine"
    assert vs.read_settings(rt.store)["speech"]["model"] == "piper-ryan"


def test_a_build_that_fails_keeps_the_engine_already_speaking(tmp_path):
    """Losing the voice because a NEW one could not be constructed is the worst shape a
    failure can take — the pick is still recorded, and the report says what happened."""
    engines = _Engines()
    rt, _ = _runtime(tmp_path, engines)
    rt.voice_update({"speech": "gateway:piper-amy"})
    standing = rt._synth
    engines.fail = {vs.SPEECH}
    out = rt.voice_update({"speech": "gateway:piper-ryan"})
    assert out["ok"] is True and rt._synth is standing
    assert "keeping the current engine" in out["applied"][vs.SPEECH]["note"]
    assert vs.read_settings(rt.store)["speech"]["model"] == "piper-ryan"


def test_a_builder_that_raises_is_reported_not_fatal(tmp_path):
    class _Angry(_Engines):
        def build_speech(self, choice):
            raise RuntimeError("no such voice on this box")

    rt, _ = _runtime(tmp_path, _Angry())
    out = rt.voice_update({"speech": "gateway:piper-ryan"})
    assert out["ok"] is True
    assert "RuntimeError" in out["applied"][vs.SPEECH]["note"]


def test_clearing_a_side_goes_back_to_the_default(tmp_path):
    rt, _ = _runtime(tmp_path, _Engines())
    rt.voice_update({"speech": "gateway:piper-ryan"})
    out = rt.voice_update({"speech": None})
    assert out["selected"][vs.SPEECH] == "gateway:piper-amy"
    assert out["chosen"][vs.SPEECH] is False
    assert "speech" not in vs.read_settings(rt.store)


def test_swapping_the_ears_drops_the_half_heard_utterance(tmp_path):
    """An `SttSession` captures its transcriber, so a swap mid-utterance would otherwise
    finish that sentence on the engine a parent just replaced."""
    rt, device_id = _runtime(tmp_path, _Engines())
    rt.voice_update({"listening": "gateway:stt-whisper"})
    rt._stt_session(device_id)
    assert rt._stt_sessions
    rt.voice_update({"listening": "gateway:graphling-stt"})
    assert rt._stt_sessions == {}


# ------------------------------------------------------------ the Test button --
def test_the_test_button_speaks_through_the_engine_that_is_installed(tmp_path):
    rt, device_id = _runtime(tmp_path, _Engines())
    rt.voice_update({"speech": "gateway:piper-ryan"})
    out = rt.voice_test(device_id)
    assert out["ok"] is True and out["spoke"] == rt.DEFAULT_VOICE_TEST_LINE
    assert out["engine"] == "fake-voice (gateway:piper-ryan)"
    assert out["sample_rate"] == 22050
    published = rt.client.on(TTS_TOPIC.format(device_id=device_id))
    assert len(published) == 1
    assert published[0]["event_id"] == out["event_id"]
    assert published[0]["chunk_num"] == 0 and published[0]["audio"]["buffer"]


def test_the_test_button_says_the_line_it_was_given(tmp_path):
    rt, device_id = _runtime(tmp_path, _Engines())
    rt.voice_update({"speech": "gateway:piper-amy"})
    out = rt.voice_test(device_id, "Testing, one two three.")
    assert out["ok"] and rt._synth.spoken == ["Testing, one two three."]


def test_the_test_button_needs_a_robot_that_is_actually_there(tmp_path):
    rt, _ = _runtime(tmp_path, _Engines())
    rt.voice_update({"speech": "gateway:piper-amy"})
    out = rt.voice_test("d_nobody")
    assert out["ok"] is False and "unknown device_id" in out["error"]


def test_the_test_button_is_honest_when_there_is_no_voice(tmp_path):
    rt, device_id = _runtime(tmp_path, _Engines())
    rt.set_synthesizer(None)
    out = rt.voice_test(device_id)
    assert out["ok"] is False and "No speech engine" in out["reason"]


# ------------------------------------------------------------------- HTTP ----
@pytest.fixture()
def served(tmp_path):
    engines = _Engines(piper=["en_US-amy-medium"], whisper=["base.en"])
    rt, device_id = _runtime(tmp_path, engines)
    return rt, device_id, status_server(rt), engines


def test_get_voice_serves_the_whole_card(served):
    _, _, base, _ = served
    out = http_json(base + "/voice")
    assert out["ok"] is True
    assert out["selected"][vs.SPEECH] == "gateway:piper-amy"
    assert {e["group"] for e in out["available"][vs.SPEECH]} == {"Gateway", "Local",
                                                                 "Built-in"}


def test_post_voice_round_trips_through_the_status_server(served):
    rt, _, base, _ = served
    out = http_json(base + "/voice", method="POST",
                    body={"speech": "gateway:piper-ryan"})
    assert out["ok"] is True and out["selected"][vs.SPEECH] == "gateway:piper-ryan"
    assert http_json(base + "/voice")["selected"][vs.SPEECH] == "gateway:piper-ryan"
    assert rt._synth.choice["model"] == "piper-ryan"


def test_post_voice_with_a_bad_pick_is_a_400_with_the_reason(served):
    import urllib.error
    _, _, base, _ = served
    with pytest.raises(urllib.error.HTTPError) as e:
        http_json(base + "/voice", method="POST", body={"speech": "gateway:nope"})
    assert e.value.code == 400
    assert "nope" in json.loads(e.value.read().decode())["reason"]


def test_post_voice_test_publishes_a_cloud_tts_response(served):
    rt, device_id, base, _ = served
    http_json(base + "/voice", method="POST", body={"speech": "gateway:piper-amy"})
    out = http_json(base + f"/voice/test?device_id={device_id}", method="POST",
                    body={"text": "Hello from the console."})
    assert out["ok"] is True and out["sample_rate"] == 22050
    published = rt.client.on(TTS_TOPIC.format(device_id=device_id))
    assert published and published[-1]["audio"]["channels"] == 1
    assert rt._synth.spoken[-1] == "Hello from the console."


def test_post_voice_test_for_a_robot_that_is_not_there_is_a_404(served):
    import urllib.error
    _, _, base, _ = served
    with pytest.raises(urllib.error.HTTPError) as e:
        http_json(base + "/voice/test?device_id=d_nobody", method="POST", body={})
    assert e.value.code == 404


# ------------------------------ the cold-supervisor race (found live 2026-09-02) ------
def test_a_save_asks_discovery_to_settle_but_the_card_never_does():
    """The live run's bug, pinned: three seconds after boot the gateway list is still in
    flight, and a `POST /voice` judged against it refused `gateway:piper-amy` with
    "choose one of: tone". A write gets a bounded wait; a read gets whatever is cached."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        engines = _Engines()
        rt, _ = _runtime(tmp, engines)
        rt.voice_view()
        assert engines.settles == [0.0], "the card's poll must never wait"
        rt.voice_update({"speech": "gateway:piper-ryan"})
        # the WRITE's own lookup waits; the view it renders afterwards does not
        assert engines.settles[1] == rt.VOICE_SETTLE_S > 0
        assert engines.settles[2] == 0.0


def test_a_cold_catalog_does_not_refuse_a_good_pick():
    """End to end through the real `GatewayCatalog`: the listing is still on its way when
    the Save arrives, and the pick is accepted rather than refused."""
    import tempfile, threading
    started, release = threading.Event(), threading.Event()

    def _slow():
        started.set()
        release.wait(5)
        return GATEWAY_MODELS

    class _ColdEngines(_Engines):
        def __init__(self):
            super().__init__()
            self.catalog = vs.GatewayCatalog(_slow, ttl_s=300)

        def available(self, *, refresh=False, settle_s=0.0):
            snap = self.catalog.snapshot(refresh=refresh, settle_s=settle_s)
            return {"available": vs.build_available(snap["ids"]),
                    "discovering": snap["discovering"],
                    "gateway_error": snap["gateway_error"]}

    with tempfile.TemporaryDirectory() as tmp:
        rt, _ = _runtime(tmp, _ColdEngines())
        cold = rt.voice_view()                       # the card renders immediately…
        assert cold["discovering"] is True
        assert vs.option_ids(cold["available"][vs.SPEECH]) == ["tone"]
        threading.Timer(0.2, release.set).start()    # …the listing lands a moment later
        out = rt.voice_update({"speech": "gateway:piper-ryan"})
        assert out["ok"] is True, out.get("reason")
        assert out["selected"][vs.SPEECH] == "gateway:piper-ryan"
