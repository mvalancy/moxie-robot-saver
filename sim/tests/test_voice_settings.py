"""
🎚️ The voice picker's pure half — `mqtt/moxie_sdk/voice_settings.py` and the `override=`
argument the two `mqtt/config.py` builders grew for it.

`test_stt_gateway.py` already pins `classify_audio_models` against the list the gateway
really served on 2026-09-02; this file pins what a console does with that list: the
entries the two dropdowns render, the "`piper-amy` when possible" default, the refusal a
stale page gets, the record that survives a restart, and the TTL that keeps discovery off
a turn's path.

Everything runs with **no `openai`, no `piper`, no `faster-whisper`** (playbook rule 9):
the gateway listing arrives through a `list_models()` fake, and the engine tests swap the
constructors `config` calls. Nothing here spends a request. The real endpoint is exercised
once, by hand, in the slice's live step (`docs/architecture/backlog/voice-picker.md`).
"""
import importlib
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MQTT = os.path.join(REPO, "mqtt")
sys.path.insert(0, MQTT)
sys.path.insert(0, os.path.join(MQTT, "supervisor"))

from moxie_sdk import voice_settings as vs                       # noqa: E402
from moxie_sdk.store import JsonStore                            # noqa: E402

#: Exactly what `GET /v1/models` served on 2026-09-02 — six voices, three ears, and chat
#: models that must not leak into either dropdown.
GATEWAY_MODELS = [
    "piper-amy", "piper-ryan", "graphling-tts-narrator", "graphling-tts-character",
    "stt-whisper", "graphling-stt", "tts-piper-amy", "tts-piper-ryan",
    "stt-whisper-base", "graphling-medium", "graphling-small", "qwen2.5-7b",
]

GW = "https://gateway.graphlings.net/v1"


def _available(gateway=GATEWAY_MODELS, piper=(), whisper=()):
    return vs.build_available(gateway, piper_voices=piper, whisper_models=whisper)


# ------------------------------------------------------------ the two lists --
def test_the_dropdowns_offer_the_gateway_the_local_engines_and_the_builtins():
    a = _available(piper=["en_US-amy-medium", "en_US-lessac-medium"],
                   whisper=["base.en"])
    assert vs.option_ids(a[vs.SPEECH]) == [
        "gateway:piper-amy", "gateway:piper-ryan", "gateway:graphling-tts-narrator",
        "gateway:graphling-tts-character", "gateway:tts-piper-amy",
        "gateway:tts-piper-ryan", "piper:en_US-amy-medium", "piper:en_US-lessac-medium",
        "tone"]
    assert vs.option_ids(a[vs.LISTENING]) == [
        "gateway:stt-whisper", "gateway:graphling-stt", "gateway:stt-whisper-base",
        "whisper:base.en", "off"]


def test_chat_models_never_reach_either_dropdown():
    a = _available()
    ids = " ".join(vs.option_ids(a[vs.SPEECH]) + vs.option_ids(a[vs.LISTENING]))
    for brain in ("graphling-medium", "graphling-small", "qwen2.5-7b"):
        assert brain not in ids, f"{brain} leaked into a voice picker"


def test_the_builtins_exist_even_with_nothing_else():
    """With no gateway and no local engines the card still renders two working choices —
    the floor that keeps a fresh clone from showing an empty picker."""
    a = _available(gateway=[])
    assert vs.option_ids(a[vs.SPEECH]) == ["tone"]
    assert vs.option_ids(a[vs.LISTENING]) == ["off"]


def test_every_entry_is_grouped_for_the_optgroups():
    a = _available(piper=["en_US-amy-medium"], whisper=["base.en"])
    groups = {e["id"]: e["group"] for e in a[vs.SPEECH] + a[vs.LISTENING]}
    assert groups["gateway:piper-amy"] == vs.GATEWAY_GROUP
    assert groups["piper:en_US-amy-medium"] == vs.LOCAL_GROUP
    assert groups["whisper:base.en"] == vs.LOCAL_GROUP
    assert groups["tone"] == groups["off"] == vs.BUILTIN_GROUP


# ----------------------------------------------------------------- labels ----
@pytest.mark.parametrize("choice,label", [
    (("gateway", "piper-amy"), "Amy (gateway, piper-amy)"),
    (("gateway", "tts-piper-ryan"), "Ryan (gateway, tts-piper-ryan)"),
    (("gateway", "graphling-tts-narrator"), "Narrator (gateway, graphling-tts-narrator)"),
    (("gateway", "stt-whisper"), "Whisper (gateway, stt-whisper)"),
    (("piper", "en_US-amy-medium"), "Amy (local Piper)"),
    (("whisper", "base.en"), "base.en (local whisper)"),
    (("tone", ""), "Tone (built-in)"),
    (("off", ""), "Off (built-in)"),
])
def test_describe_choice_is_something_a_parent_can_read(choice, label):
    assert vs.describe_choice(vs.make_choice(*choice)) == label


def test_a_label_never_invents_a_word_it_cannot_find():
    """`graphling-stt` is all plumbing — the raw id is a better label than a guess."""
    assert vs.describe_choice(vs.make_choice("gateway", "graphling-stt")) == \
        "graphling-stt (gateway, graphling-stt)"


def test_the_ids_round_trip_through_the_dropdowns_value():
    for raw in ("gateway:piper-amy", "piper:en_US-amy-medium", "tone", "off"):
        assert vs.choice_id(vs.parse_choice(raw)) == raw


# ---------------------------------------------------------------- defaults ---
def test_the_default_voice_is_piper_amy_whenever_the_gateway_serves_it():
    d = vs.resolve_defaults(_available())
    assert d[vs.SPEECH] == {"engine": "gateway", "model": "piper-amy"}
    assert d[vs.LISTENING] == {"engine": "gateway", "model": "stt-whisper"}


def test_a_gateway_without_moxies_own_voice_falls_to_its_first_one():
    a = _available(gateway=["piper-ryan", "graphling-tts-narrator", "graphling-stt"])
    d = vs.resolve_defaults(a)
    assert d[vs.SPEECH] == {"engine": "gateway", "model": "piper-ryan"}
    assert d[vs.LISTENING] == {"engine": "gateway", "model": "graphling-stt"}


def test_with_no_gateway_the_defaults_are_the_local_engines():
    a = _available(gateway=[], piper=["en_US-lessac-medium", "en_US-amy-medium"],
                   whisper=["base.en"])
    d = vs.resolve_defaults(a)
    # Amy is preferred among installed Piper voices even when she is not listed first…
    assert d[vs.SPEECH] == {"engine": "piper", "model": "en_US-amy-medium"}
    assert d[vs.LISTENING] == {"engine": "whisper", "model": "base.en"}


def test_with_no_amy_installed_the_first_local_voice_wins():
    a = _available(gateway=[], piper=["en_US-lessac-medium"])
    assert vs.resolve_defaults(a)[vs.SPEECH] == {"engine": "piper",
                                                 "model": "en_US-lessac-medium"}


def test_with_nothing_at_all_the_defaults_are_the_builtins():
    d = vs.resolve_defaults(_available(gateway=[]))
    assert d[vs.SPEECH] == {"engine": "tone", "model": ""}
    assert d[vs.LISTENING] == {"engine": "off", "model": ""}


def test_the_default_entry_is_marked_for_the_card():
    a = _available()
    marked = vs.mark_defaults(a, vs.resolve_defaults(a))
    flagged = [e["id"] for e in marked[vs.SPEECH] if e["default"]]
    assert flagged == ["gateway:piper-amy"]


# ------------------------------------------------------------ normalizing ----
def test_a_listed_id_is_accepted_in_either_shape():
    a = _available()
    by_id = vs.normalize_voice_settings({"speech": "gateway:piper-ryan"}, a, now=1)
    by_dict = vs.normalize_voice_settings(
        {"speech": {"engine": "gateway", "model": "piper-ryan"}}, a, now=1)
    assert by_id == by_dict == {"speech": {"engine": "gateway", "model": "piper-ryan"},
                                "updated_at": 1}


def test_an_unlisted_id_is_refused_with_a_sentence_the_console_shows():
    with pytest.raises(ValueError) as e:
        vs.normalize_voice_settings({"speech": "gateway:piper-bob"}, _available())
    msg = str(e.value)
    assert "piper-bob" in msg and "speech options" in msg
    assert "gateway:piper-amy" in msg, "the refusal must say what IS available"


def test_a_voice_cannot_be_chosen_as_the_ears():
    """The two lists are separate on purpose — `piper-amy` cannot transcribe."""
    with pytest.raises(ValueError):
        vs.normalize_voice_settings({"listening": "gateway:piper-amy"}, _available())


def test_one_side_can_be_changed_without_disturbing_the_other():
    a = _available()
    first = vs.normalize_voice_settings({"speech": "gateway:piper-ryan"}, a, now=1)
    second = vs.normalize_voice_settings({"listening": "gateway:graphling-stt"}, a,
                                         current=first, now=2)
    assert second["speech"] == {"engine": "gateway", "model": "piper-ryan"}
    assert second["listening"] == {"engine": "gateway", "model": "graphling-stt"}


def test_null_clears_a_choice_back_to_the_default():
    a = _available()
    picked = vs.normalize_voice_settings({"speech": "gateway:piper-ryan"}, a, now=1)
    cleared = vs.normalize_voice_settings({"speech": None}, a, current=picked, now=2)
    assert vs.SPEECH not in cleared
    assert vs.resolve_settings(cleared, a)["current"][vs.SPEECH]["model"] == "piper-amy"


def test_a_patch_with_nothing_in_it_is_an_error_not_a_silent_write():
    with pytest.raises(ValueError):
        vs.normalize_voice_settings({}, _available())
    with pytest.raises(ValueError):
        vs.normalize_voice_settings({"volume": 3}, _available())


# ------------------------------------------------------- what is in force ----
def test_an_unset_side_uses_the_default_computed_right_now():
    a = _available()
    r = vs.resolve_settings({}, a)
    assert r["current"] == r["defaults"]
    assert r["chosen"] == {vs.SPEECH: False, vs.LISTENING: False}


def test_a_stored_choice_wins_over_the_default():
    a = _available()
    stored = {"speech": {"engine": "gateway", "model": "piper-ryan"}}
    r = vs.resolve_settings(stored, a)
    assert r["current"][vs.SPEECH]["model"] == "piper-ryan" and r["chosen"][vs.SPEECH]
    assert not r["chosen"][vs.LISTENING]


def test_a_gateway_outage_never_reverts_a_parents_choice():
    """Acceptance 5. Discovery came back with the local entries only, but the pick a
    parent made is still what is in force — the card renders it, and the engine builder
    (not this module) decides whether it can be honoured this second."""
    stored = {"speech": {"engine": "gateway", "model": "piper-amy"}}
    r = vs.resolve_settings(stored, _available(gateway=[]))
    assert r["current"][vs.SPEECH] == {"engine": "gateway", "model": "piper-amy"}
    assert r["chosen"][vs.SPEECH]


def test_a_malformed_stored_choice_degrades_to_the_default():
    for junk in ({"speech": "banana"}, {"speech": {"engine": "gateway"}},
                 {"speech": 7}, {"speech": None}):
        r = vs.resolve_settings(junk, _available())
        assert r["current"][vs.SPEECH]["model"] == "piper-amy"
        assert not r["chosen"][vs.SPEECH]


# ------------------------------------------------------------ persistence ----
def test_the_record_round_trips_through_the_json_store(tmp_path):
    store = JsonStore(str(tmp_path))
    a = _available()
    settings = vs.normalize_voice_settings(
        {"speech": "gateway:piper-ryan", "listening": "gateway:stt-whisper"}, a, now=99)
    assert vs.write_settings(store, settings)
    assert os.path.isfile(os.path.join(str(tmp_path), "fleet", "voice.json"))
    back = vs.read_settings(store)
    assert back == settings
    assert vs.resolve_settings(back, a)["current"][vs.SPEECH]["model"] == "piper-ryan"


def test_no_record_at_all_reads_as_empty(tmp_path):
    assert vs.read_settings(JsonStore(str(tmp_path))) == {}


def test_a_hand_broken_record_never_stops_a_boot(tmp_path):
    store = JsonStore(str(tmp_path))
    store.write_shared(vs.COLLECTION, {"speech": "not-an-engine:x", "updated_at": "soon"})
    assert vs.read_settings(store) == {}


# ------------------------------------------------------- local Piper voices --
def test_installed_piper_voices_are_found_by_file(tmp_path):
    (tmp_path / "en_US-amy-medium.onnx").write_bytes(b"x")
    (tmp_path / "en_US-lessac-medium.onnx").write_bytes(b"x")
    (tmp_path / "en_US-amy-medium.onnx.json").write_text("{}")     # config, not a voice
    assert vs.piper_voices(voices_dir=str(tmp_path)) == ["en_US-amy-medium",
                                                         "en_US-lessac-medium"]


def test_the_configured_model_comes_first_and_is_never_duplicated(tmp_path):
    (tmp_path / "en_US-amy-medium.onnx").write_bytes(b"x")
    (tmp_path / "en_US-lessac-medium.onnx").write_bytes(b"x")
    got = vs.piper_voices(str(tmp_path / "en_US-lessac-medium.onnx"), str(tmp_path))
    assert got == ["en_US-lessac-medium", "en_US-amy-medium"]


def test_a_missing_voices_directory_is_an_empty_list_not_an_error():
    assert vs.piper_voices(voices_dir="/nope/not/here") == []


def test_a_voice_name_resolves_back_to_its_file(tmp_path):
    (tmp_path / "en_US-amy-medium.onnx").write_bytes(b"x")
    assert vs.piper_voice_path("en_US-amy-medium", voices_dir=str(tmp_path)) == \
        str(tmp_path / "en_US-amy-medium.onnx")
    assert vs.piper_voice_path("en_US-nobody", voices_dir=str(tmp_path)) == ""
    # MOXIE_PIPER_MODEL wins for its own voice, wherever the file happens to live
    assert vs.piper_voice_path("weird", "/elsewhere/weird.onnx", str(tmp_path)) == \
        "/elsewhere/weird.onnx"


# --------------------------------------------------------------- discovery ---
class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _catalog(results, *, ttl_s=300.0, clock=None):
    """A `GatewayCatalog` over a scripted `models.list()` — synchronous, so a test reads
    the fresh state in the same call (`submit=lambda fn: fn()`)."""
    calls = []

    def _list():
        calls.append(1)
        out = results[min(len(calls) - 1, len(results) - 1)]
        if isinstance(out, Exception):
            raise out
        return out

    cat = vs.GatewayCatalog(_list, ttl_s=ttl_s, clock=clock or _Clock(),
                            submit=lambda fn: fn())
    return cat, calls


def test_a_gateway_listing_becomes_the_two_dropdowns():
    cat, calls = _catalog([GATEWAY_MODELS])
    snap = cat.snapshot()
    assert len(calls) == 1 and not snap["gateway_error"] and not snap["discovering"]
    a = vs.build_available(snap["ids"])
    assert "gateway:piper-amy" in vs.option_ids(a[vs.SPEECH])
    assert "gateway:stt-whisper" in vs.option_ids(a[vs.LISTENING])


def test_a_gateway_that_refuses_leaves_the_local_entries_and_names_the_failure():
    cat, _ = _catalog([RuntimeError("502 Bad Gateway")])
    snap = cat.snapshot()
    assert snap["ids"] == [] and snap["gateway_error"] == "RuntimeError"
    a = vs.build_available(snap["ids"], piper_voices=["en_US-amy-medium"])
    assert vs.option_ids(a[vs.SPEECH]) == ["piper:en_US-amy-medium", "tone"]


def test_an_outage_after_a_good_listing_keeps_the_options_it_already_had():
    """A card that empties itself when a proxy hiccups is worse than one that says the
    gateway is unreachable beside the options it already had."""
    clock = _Clock()
    cat, calls = _catalog([GATEWAY_MODELS, RuntimeError("boom")], ttl_s=10, clock=clock)
    assert cat.snapshot()["ids"] == GATEWAY_MODELS
    clock.t += 60
    snap = cat.snapshot()
    assert snap["ids"] == GATEWAY_MODELS and snap["gateway_error"] == "RuntimeError"
    assert len(calls) == 2


def test_the_cache_spends_one_listing_per_ttl_window():
    clock = _Clock()
    cat, calls = _catalog([GATEWAY_MODELS], ttl_s=300, clock=clock)
    for _ in range(5):
        cat.snapshot()
    assert len(calls) == 1, "discovery must not cost a request per page load"
    clock.t += 299
    cat.snapshot()
    assert len(calls) == 1
    clock.t += 2                                   # past the window
    cat.snapshot()
    assert len(calls) == 2


def test_an_explicit_refresh_beats_the_cache():
    cat, calls = _catalog([GATEWAY_MODELS], ttl_s=300)
    cat.snapshot()
    cat.snapshot(refresh=True)
    assert len(calls) == 2


def test_the_first_ask_answers_immediately_while_the_request_is_still_in_flight():
    """Acceptance 5 / "never blocks a turn": with a real background submit the caller gets
    `discovering: true` and the local entries, not a network wait."""
    import threading
    started, release = threading.Event(), threading.Event()

    def _slow():
        started.set()
        release.wait(5)
        return GATEWAY_MODELS

    cat = vs.GatewayCatalog(_slow, ttl_s=300)
    snap = cat.snapshot()
    assert started.wait(5), "discovery never started"
    assert snap["ids"] == [] and snap["discovering"] is True
    release.set()
    for _ in range(500):                            # the background thread fills it in
        if cat.snapshot()["ids"]:
            break
        import time as _t
        _t.sleep(0.01)
    assert cat.snapshot()["ids"] == GATEWAY_MODELS


def test_no_gateway_configured_means_no_listing_is_ever_attempted():
    cat = vs.GatewayCatalog(None)
    assert not cat.configured
    assert cat.snapshot() == {"ids": [], "gateway_error": "", "discovering": False,
                              "fetched_at": 0.0}


# ------------------------------------------------- the builders' override= ---
_ENV = ("MOXIE_TTS", "MOXIE_STT", "MOXIE_STT_MODEL", "MOXIE_STT_BASE_URL",
        "MOXIE_STT_API_KEY", "MOXIE_VOICE_BASE_URL", "MOXIE_VOICE_API_KEY",
        "MOXIE_LLM_API_KEY", "MOXIE_PIPER_MODEL", "MOXIE_PIPER_CONFIG",
        "MOXIE_VOICES_DIR", "MOXIE_APP")


def _fresh_config(monkeypatch, **env):
    # The dotenv opt-out comes first: `config._load_env` would otherwise refill every
    # variable deleted below from a real `mqtt/.env` (playbook rule 20).
    monkeypatch.setenv("MOXIE_SKIP_DOTENV", "1")
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import config as _c
    return importlib.reload(_c)


def _stub_tts(monkeypatch, built):
    """Swap both voice constructors for recorders — no piper wheels, no openai."""
    import moxie_sdk.tts as tts

    class _Piper(tts.Synthesizer):
        name = "piper"
        sample_rate = 22050

        def __init__(self, path):
            self.path = path

        def describe(self):
            return f"piper ({os.path.basename(self.path)})"

        def synthesize(self, text, voice=None):
            return b"\x01\x02"

    class _Gateway(tts.Synthesizer):
        name = "openai-voice"
        sample_rate = 22050

        def __init__(self, model):
            self.model = model

        def describe(self):
            return f"openai-voice ({self.model})"

        def synthesize(self, text, voice=None):
            return b"\x03\x04"

    def _make_piper(model_path, config_path=None, **kw):
        if not model_path:
            return None
        built["piper"].append(model_path)
        return _Piper(model_path)

    def _make_voice(base_url, api_key, voice=None, **kw):
        if not base_url:
            return None
        built["gateway"].append(kw.get("model"))
        return _Gateway(kw.get("model"))

    monkeypatch.setattr(tts, "make_piper_synthesizer", _make_piper)
    monkeypatch.setattr(tts, "make_voice_synthesizer", _make_voice)


@pytest.fixture()
def voices(tmp_path):
    (tmp_path / "en_US-amy-medium.onnx").write_bytes(b"x")
    (tmp_path / "en_US-lessac-medium.onnx").write_bytes(b"x")
    return str(tmp_path)


def test_no_override_keeps_todays_behaviour_byte_for_byte(monkeypatch):
    """The whole compatibility promise of this slice: an appliance nobody has touched in
    the console builds exactly the engine it built before the picker existed."""
    built = {"piper": [], "gateway": []}
    _stub_tts(monkeypatch, built)
    c = _fresh_config(monkeypatch, MOXIE_STT="off", MOXIE_VOICE_BASE_URL=GW,
                      MOXIE_LLM_API_KEY="sk-test-not-a-real-key-0000")
    synth = c.build_synthesizer()
    # `FallbackSynthesizer.describe()` names the engines; the MODEL it was handed is the
    # thing this test is about, and `built` is where the constructor recorded it.
    assert synth.describe() == "openai-voice (standby: tone)"
    assert built["gateway"] == ["piper-amy"]


def test_a_picked_gateway_voice_is_the_one_that_is_built(monkeypatch):
    built = {"piper": [], "gateway": []}
    _stub_tts(monkeypatch, built)
    c = _fresh_config(monkeypatch, MOXIE_STT="off", MOXIE_VOICE_BASE_URL=GW,
                      MOXIE_LLM_API_KEY="sk-test-not-a-real-key-0000")
    synth = c.build_synthesizer(override={"engine": "gateway", "model": "piper-ryan"})
    assert built["gateway"] == ["piper-ryan"], "the picked model never reached the gateway"
    assert synth.describe() == "openai-voice (standby: tone)"


def test_a_picked_local_voice_wins_even_with_a_gateway_configured(monkeypatch, voices):
    """Acceptance 4 / the owner rule, for the voice: an explicit LOCAL choice is honoured
    with `MOXIE_VOICE_BASE_URL` fully set — the same statement `MOXIE_TTS=piper` makes."""
    built = {"piper": [], "gateway": []}
    _stub_tts(monkeypatch, built)
    c = _fresh_config(monkeypatch, MOXIE_STT="off", MOXIE_VOICE_BASE_URL=GW,
                      MOXIE_VOICES_DIR=voices,
                      MOXIE_LLM_API_KEY="sk-test-not-a-real-key-0000")
    synth = c.build_synthesizer(override={"engine": "piper",
                                          "model": "en_US-lessac-medium"})
    assert synth.name == "piper" and built["gateway"] == []
    assert built["piper"] == [os.path.join(voices, "en_US-lessac-medium.onnx")]


def test_a_picked_tone_is_honoured(monkeypatch):
    _stub_tts(monkeypatch, {"piper": [], "gateway": []})
    c = _fresh_config(monkeypatch, MOXIE_STT="off", MOXIE_VOICE_BASE_URL=GW,
                      MOXIE_LLM_API_KEY="sk-test-not-a-real-key-0000")
    assert c.build_synthesizer(override={"engine": "tone", "model": ""}).name == "tone"


def test_a_pick_that_cannot_be_built_here_falls_back_instead_of_going_silent(monkeypatch):
    """A voice file that vanished must not cost a child their voice: the env path takes
    over and the boot line says which engine actually got installed."""
    built = {"piper": [], "gateway": []}
    _stub_tts(monkeypatch, built)
    c = _fresh_config(monkeypatch, MOXIE_STT="off", MOXIE_VOICE_BASE_URL=GW,
                      MOXIE_VOICES_DIR="/nope", MOXIE_LLM_API_KEY="sk-test-0000")
    synth = c.build_synthesizer(override={"engine": "piper", "model": "en_US-gone"})
    assert synth.describe() == "openai-voice (standby: tone)"
    assert built["gateway"] == ["piper-amy"] and built["piper"] == []


def test_tts_off_still_wins_over_a_pick(monkeypatch):
    """`MOXIE_TTS=off` is a deployment declaring itself voiceless — a dropdown does not
    talk it back into speaking."""
    _stub_tts(monkeypatch, {"piper": [], "gateway": []})
    c = _fresh_config(monkeypatch, MOXIE_TTS="off", MOXIE_STT="off",
                      MOXIE_VOICE_BASE_URL=GW)
    assert c.build_synthesizer(override={"engine": "tone", "model": ""}) is None


def _stub_stt(monkeypatch, built, *, whisper=True):
    import moxie_sdk.stt as stt

    class _Whisper(stt.Transcriber):
        name = "faster-whisper"

        def __init__(self, model="base.en", **kw):
            self.model = model
            built["whisper"].append(model)

        def describe(self):
            return f"faster-whisper ({self.model})"

        def transcribe(self, pcm, sample_rate=16000):
            return "local"

        @classmethod
        def available(cls):
            return whisper

    class _Gateway(stt.Transcriber):
        name = "openai-stt"

        def __init__(self, model):
            self.model = model

        def describe(self):
            return f"openai-stt ({self.model})"

        def transcribe(self, pcm, sample_rate=16000):
            return "cloud"

    def _make(base_url, api_key, model="stt-whisper", **kw):
        if not base_url:
            return None
        built["gateway"].append(model)
        return _Gateway(model)

    monkeypatch.setattr(stt, "WhisperTranscriber", _Whisper)
    monkeypatch.setattr(stt, "make_openai_transcriber", _make)
    monkeypatch.setattr(stt.OpenAITranscriber, "available",
                        classmethod(lambda cls, base_url="": bool(base_url)))


def test_a_picked_gateway_model_is_the_one_the_ears_use(monkeypatch):
    built = {"whisper": [], "gateway": []}
    _stub_stt(monkeypatch, built)
    c = _fresh_config(monkeypatch, MOXIE_TTS="off", MOXIE_STT_BASE_URL=GW)
    t = c.build_transcriber(override={"engine": "gateway", "model": "graphling-stt"})
    assert built["gateway"] == ["graphling-stt"]
    assert t.describe() == "openai-stt (graphling-stt) (standby: faster-whisper (base.en))"


def test_a_picked_local_whisper_wins_even_with_a_gateway_configured(monkeypatch):
    """Acceptance 4 for the ears — a home appliance keeps a child's voice in the house."""
    built = {"whisper": [], "gateway": []}
    _stub_stt(monkeypatch, built)
    c = _fresh_config(monkeypatch, MOXIE_TTS="off", MOXIE_STT_BASE_URL=GW,
                      MOXIE_STT_API_KEY="sk-test-not-a-real-key-0000")
    t = c.build_transcriber(override={"engine": "whisper", "model": "base.en"})
    assert t.name == "faster-whisper" and built["gateway"] == []


def test_picking_off_really_turns_the_ears_off(monkeypatch):
    built = {"whisper": [], "gateway": []}
    _stub_stt(monkeypatch, built)
    c = _fresh_config(monkeypatch, MOXIE_TTS="off", MOXIE_STT_BASE_URL=GW)
    assert c.build_transcriber(override={"engine": "off", "model": ""}) is None
    assert built["gateway"] == []


def test_a_picked_whisper_without_the_wheels_falls_back_to_the_env_path(monkeypatch):
    built = {"whisper": [], "gateway": []}
    _stub_stt(monkeypatch, built, whisper=False)
    c = _fresh_config(monkeypatch, MOXIE_TTS="off", MOXIE_STT="gateway",
                      MOXIE_STT_BASE_URL=GW)
    t = c.build_transcriber(override={"engine": "whisper", "model": "base.en"})
    assert t.engine_name == "openai-stt", "a missing local model must not deafen the box"


def test_no_override_keeps_todays_ears_byte_for_byte(monkeypatch):
    built = {"whisper": [], "gateway": []}
    _stub_stt(monkeypatch, built)
    c = _fresh_config(monkeypatch, MOXIE_TTS="off", MOXIE_STT="auto",
                      MOXIE_VOICE_BASE_URL=GW, MOXIE_LLM_API_KEY="sk-test-0000")
    t = c.build_transcriber()
    assert t.engine_name == "openai-stt" and built["gateway"] == ["stt-whisper"]


# ------------------------------------------------------- what config offers --
def test_config_offers_only_local_engines_it_can_really_build(monkeypatch, voices):
    """Honesty rule: a dropdown entry is a promise. With no `piper` package installed the
    voices on disk are not offered, because choosing one could not work."""
    import moxie_sdk.tts as tts
    c = _fresh_config(monkeypatch, MOXIE_TTS="off", MOXIE_STT="off",
                      MOXIE_VOICES_DIR=voices)
    monkeypatch.setattr(tts.PiperSynthesizer, "available", classmethod(lambda cls: False))
    assert c.local_piper_voices() == []
    monkeypatch.setattr(tts.PiperSynthesizer, "available", classmethod(lambda cls: True))
    assert c.local_piper_voices() == ["en_US-amy-medium", "en_US-lessac-medium"]


def test_config_offers_whisper_sizes_it_would_not_have_to_download(monkeypatch):
    import moxie_sdk.stt as stt
    c = _fresh_config(monkeypatch, MOXIE_TTS="off", MOXIE_STT="off",
                      MOXIE_STT_MODEL="small.en")
    monkeypatch.setattr(stt.WhisperTranscriber, "available", classmethod(lambda cls: True))
    assert c.local_whisper_models() == ["base.en", "small.en"]
    monkeypatch.setattr(stt.WhisperTranscriber, "available", classmethod(lambda cls: False))
    assert c.local_whisper_models() == []


def test_the_appliance_adapter_never_lists_a_gateway_it_has_no_url_for(monkeypatch):
    # No `MOXIE_TTS`/`MOXIE_STT` here on purpose: an explicit value now PINS the engine
    # and would filter these very lists (see the pin tests below). This test is about a
    # missing gateway URL, so it leaves the environment saying nothing.
    c = _fresh_config(monkeypatch)
    engines = c.voice_engines()
    assert not engines.catalog.configured
    out = engines.available()
    assert out["gateway_error"] == "" and out["discovering"] is False
    assert vs.option_ids(out["available"][vs.SPEECH])[-1] == "tone"


def test_the_appliance_adapter_turns_a_listing_into_the_two_dropdowns(monkeypatch):
    c = _fresh_config(monkeypatch)          # unpinned — see the pin tests below
    cat = vs.GatewayCatalog(lambda: GATEWAY_MODELS, submit=lambda fn: fn())
    out = c.voice_engines(cat).available()
    assert "gateway:piper-amy" in vs.option_ids(out["available"][vs.SPEECH])
    assert "gateway:stt-whisper" in vs.option_ids(out["available"][vs.LISTENING])


# ------------------------------------------------- the environment's pin -----
# The owner rule, from the other direction: `MOXIE_TTS=piper` / `MOXIE_STT=whisper` are an
# OPERATOR'S statement that this deployment runs local engines, and a dropdown must not be
# able to move it off them. Before this section the console pick sat above every env value
# except `off`, so a pick of `gateway:piper-ryan` silently overruled `MOXIE_TTS=piper` —
# the exact "picker silently overrides an explicit operator setting" bug.

def test_an_explicit_value_pins_an_engine_and_auto_pins_nothing():
    assert vs.pin_for_env(vs.SPEECH, "piper") == "piper"
    assert vs.pin_for_env(vs.SPEECH, "local") == "piper"        # the documented alias
    assert vs.pin_for_env(vs.SPEECH, "GATEWAY") == "gateway"    # case-insensitive
    assert vs.pin_for_env(vs.SPEECH, "openai") == "gateway"
    assert vs.pin_for_env(vs.SPEECH, "off") == "off"
    # `tone` is a PERMISSION, not a selection (`config.build_synthesizer` reaches it only
    # after the gateway and Piper), and it is what BOTH compose files default to. Pinning
    # it would cut every `docker compose up` deployment's Speech dropdown down to one
    # entry — so it pins nothing, and this assertion is the guard on that.
    assert vs.pin_for_env(vs.SPEECH, "tone") == ""
    assert vs.pin_for_env(vs.LISTENING, "whisper") == "whisper"
    assert vs.pin_for_env(vs.LISTENING, "local") == "whisper"
    # Everything that means "decide for me" — which is what the picker is for.
    for nothing in ("", "  ", "auto", None, "banana", "tone"):
        assert vs.pin_for_env(vs.LISTENING, nothing) == "", nothing
        assert vs.pin_for_env(vs.SPEECH, nothing) == "", nothing


def test_honours_pin_allows_another_model_but_never_another_engine():
    gw = vs.make_choice("gateway", "piper-ryan")
    assert vs.honours_pin(vs.SPEECH, gw, "") is True          # nothing pinned
    assert vs.honours_pin(vs.SPEECH, gw, "gateway") is True   # same engine, any model
    assert vs.honours_pin(vs.SPEECH, gw, "piper") is False    # a different engine
    assert vs.honours_pin(vs.SPEECH, None, "piper") is False  # nothing to honour it with


def test_the_pin_note_names_the_variable_that_did_it():
    note = vs.pin_note(vs.SPEECH, "piper")
    assert "MOXIE_TTS=piper" in note and "local Piper" in note
    assert "MOXIE_STT=gateway" in vs.pin_note(vs.LISTENING, "gateway")
    assert "no ears" in vs.pin_note(vs.LISTENING, "off")
    assert vs.pin_note(vs.SPEECH, "auto") == "" and vs.pin_note(vs.SPEECH, "") == ""


def test_a_pinned_side_offers_only_that_engines_entries():
    a = _available(piper=["en_US-amy-medium"], whisper=["base.en"])
    out = vs.filter_available(a, {vs.SPEECH: "piper", vs.LISTENING: ""})
    assert vs.option_ids(out[vs.SPEECH]) == ["piper:en_US-amy-medium"]
    # The unpinned side is untouched — one variable pins one side.
    assert vs.option_ids(out[vs.LISTENING]) == vs.option_ids(a[vs.LISTENING])
    # `off` for the voice means there is nothing to pick, and saying so beats offering a
    # `tone` this deployment would refuse to install.
    assert vs.filter_available(a, {vs.SPEECH: "off"})[vs.SPEECH] == []


# --- and the same rule where it actually bites: the builders -------------------
def test_an_explicit_moxie_tts_piper_is_not_overruled_by_a_gateway_pick(monkeypatch, voices):
    """THE BUG. `MOXIE_TTS=piper` with a gateway fully configured is the owner's "local
    stays first-class" written into the environment; a console pick of a gateway voice
    must not quietly move this house off its local voice."""
    built = {"piper": [], "gateway": []}
    _stub_tts(monkeypatch, built)
    c = _fresh_config(monkeypatch, MOXIE_TTS="piper", MOXIE_STT="off",
                      MOXIE_VOICE_BASE_URL=GW, MOXIE_VOICES_DIR=voices,
                      MOXIE_PIPER_MODEL=os.path.join(voices, "en_US-amy-medium.onnx"),
                      MOXIE_LLM_API_KEY="sk-test-not-a-real-key-0000")
    synth = c.build_synthesizer(override={"engine": "gateway", "model": "piper-ryan"})
    assert synth.name == "piper", "the pick overruled an explicit MOXIE_TTS"
    assert built["gateway"] == [], "the picked gateway voice was built anyway"


def test_a_pick_within_the_pinned_engine_still_chooses_the_voice(monkeypatch, voices):
    """The pin names the ENGINE, not the voice: `MOXIE_TTS=piper` still leaves a parent
    free to choose which installed Piper voice speaks."""
    built = {"piper": [], "gateway": []}
    _stub_tts(monkeypatch, built)
    c = _fresh_config(monkeypatch, MOXIE_TTS="piper", MOXIE_STT="off",
                      MOXIE_VOICES_DIR=voices,
                      MOXIE_PIPER_MODEL=os.path.join(voices, "en_US-amy-medium.onnx"))
    synth = c.build_synthesizer(override={"engine": "piper",
                                          "model": "en_US-lessac-medium"})
    assert synth.name == "piper"
    assert built["piper"][-1] == os.path.join(voices, "en_US-lessac-medium.onnx")


def test_moxie_tts_gateway_pins_the_engine_but_the_model_stays_pickable(monkeypatch, voices):
    built = {"piper": [], "gateway": []}
    _stub_tts(monkeypatch, built)
    c = _fresh_config(monkeypatch, MOXIE_TTS="gateway", MOXIE_STT="off",
                      MOXIE_VOICE_BASE_URL=GW, MOXIE_VOICES_DIR=voices,
                      MOXIE_LLM_API_KEY="sk-test-not-a-real-key-0000")
    c.build_synthesizer(override={"engine": "gateway", "model": "piper-ryan"})
    assert built["gateway"] == ["piper-ryan"]          # the model pick got through
    c.build_synthesizer(override={"engine": "piper", "model": "en_US-amy-medium"})
    assert built["piper"] == [], "a local pick overruled an explicit MOXIE_TTS=gateway"
    assert built["gateway"][-1] == "piper-amy"         # the env default model


def test_an_explicit_moxie_stt_whisper_is_not_overruled_by_a_gateway_pick(monkeypatch):
    """The ears' half of the same rule — a child's voice stays in the house."""
    built = {"whisper": [], "gateway": []}
    _stub_stt(monkeypatch, built)
    c = _fresh_config(monkeypatch, MOXIE_TTS="off", MOXIE_STT="whisper",
                      MOXIE_STT_BASE_URL=GW, MOXIE_STT_API_KEY="sk-test-0000")
    trans = c.build_transcriber(override={"engine": "gateway", "model": "graphling-stt"})
    assert trans.name == "faster-whisper"
    assert built["gateway"] == [], "the picked gateway ears were built anyway"


def test_a_pick_within_the_pinned_ears_still_chooses_the_size(monkeypatch):
    built = {"whisper": [], "gateway": []}
    _stub_stt(monkeypatch, built)
    c = _fresh_config(monkeypatch, MOXIE_TTS="off", MOXIE_STT="whisper")
    c.build_transcriber(override={"engine": "whisper", "model": "small.en"})
    assert built["whisper"] == ["small.en"]


def test_moxie_stt_gateway_pins_the_ears_but_the_model_stays_pickable(monkeypatch):
    built = {"whisper": [], "gateway": []}
    _stub_stt(monkeypatch, built)
    c = _fresh_config(monkeypatch, MOXIE_TTS="off", MOXIE_STT="gateway",
                      MOXIE_STT_BASE_URL=GW, MOXIE_STT_API_KEY="sk-test-0000")
    c.build_transcriber(override={"engine": "gateway", "model": "graphling-stt"})
    assert built["gateway"] == ["graphling-stt"]
    c.build_transcriber(override={"engine": "whisper", "model": "base.en"})
    assert built["gateway"][-1] == "stt-whisper", "the env default model was not restored"


def test_the_dropdown_offers_only_what_the_pin_would_install(monkeypatch, voices):
    """The two halves agree by construction: what the card offers is what the builders
    would accept, so a parent cannot pick something that then quietly does nothing."""
    import moxie_sdk.tts as tts
    c = _fresh_config(monkeypatch, MOXIE_TTS="piper", MOXIE_VOICES_DIR=voices)
    monkeypatch.setattr(tts.PiperSynthesizer, "available", classmethod(lambda cls: True))
    cat = vs.GatewayCatalog(lambda: GATEWAY_MODELS, submit=lambda fn: fn())
    out = c.voice_engines(cat).available()
    assert vs.option_ids(out["available"][vs.SPEECH]) == ["piper:en_US-amy-medium",
                                                          "piper:en_US-lessac-medium"]
    assert "gateway:stt-whisper" in vs.option_ids(out["available"][vs.LISTENING])
    assert out["pins"] == {vs.SPEECH: "piper", vs.LISTENING: ""}
    assert "MOXIE_TTS=piper" in out["pin_notes"][vs.SPEECH]
    assert out["pin_notes"][vs.LISTENING] == ""


def test_the_compose_default_leaves_the_whole_picker_in_charge(monkeypatch):
    """Both compose files ship `MOXIE_TTS=tone` / `MOXIE_STT=auto`. Neither selects an
    engine — `tone` is the last rung `build_synthesizer` reaches, `auto` is the absence of
    a choice — so the dropdowns must be exactly as full as with nothing set at all.
    `sim/tests/test_compose.py` guards the other end of this coupling: that the defaults
    in the two files are still values that pin nothing."""
    c = _fresh_config(monkeypatch, MOXIE_TTS="tone", MOXIE_STT="auto")
    cat = vs.GatewayCatalog(lambda: GATEWAY_MODELS, submit=lambda fn: fn())
    out = c.voice_engines(cat).available()
    assert "gateway:piper-amy" in vs.option_ids(out["available"][vs.SPEECH])
    assert "gateway:stt-whisper" in vs.option_ids(out["available"][vs.LISTENING])
    assert out["pins"] == {vs.SPEECH: "", vs.LISTENING: ""}
    assert out["pin_notes"] == {vs.SPEECH: "", vs.LISTENING: ""}


def test_a_voiceless_deployment_offers_nothing_and_says_which_variable_did_it(monkeypatch):
    c = _fresh_config(monkeypatch, MOXIE_TTS="off", MOXIE_STT="off")
    out = c.voice_engines(vs.GatewayCatalog(lambda: GATEWAY_MODELS,
                                            submit=lambda fn: fn())).available()
    assert out["available"][vs.SPEECH] == []
    assert vs.option_ids(out["available"][vs.LISTENING]) == ["off"]
    assert "MOXIE_TTS=off" in out["pin_notes"][vs.SPEECH]


# ---------------------------------------------------------- the boot lines ---
def test_the_boot_line_says_what_was_installed_and_why():
    assert vs.boot_line("speech", {"engine": "gateway", "model": "piper-amy"},
                        chosen=True) == "speech: piper-amy (gateway, chosen)"
    assert vs.boot_line("speech", {"engine": "tone", "model": ""}, chosen=False,
                        note="gateway unreachable") == \
        "speech: tone (built-in, default — gateway unreachable)"
    assert vs.boot_line("listening", {"engine": "whisper", "model": "base.en"},
                        chosen=True) == "listening: base.en (local whisper, chosen)"


# --------------------------------- the cold-supervisor race (found live 2026-09-02) ---
# `GET /voice` deliberately answers before the first listing lands. `POST /voice` must
# NOT: judged against a catalog that is still empty, a perfectly good `gateway:piper-amy`
# is refused with "choose one of: tone". That is exactly what the live run hit three
# seconds after boot, so `snapshot(settle_s=…)` gives a WRITE a bounded wait.

def test_a_write_may_wait_for_the_first_listing_a_read_never_does():
    import threading
    started, release = threading.Event(), threading.Event()

    def _slow():
        started.set()
        release.wait(5)
        return GATEWAY_MODELS

    cat = vs.GatewayCatalog(_slow, ttl_s=300)
    assert cat.snapshot()["ids"] == [], "a read must not wait on the network"
    assert started.wait(5)
    release.set()
    assert cat.snapshot(settle_s=5)["ids"] == GATEWAY_MODELS


def test_settling_gives_up_rather_than_hanging_on_a_dead_gateway():
    """Bounded, always: a gateway that never answers costs a parent one slow Save, not a
    wedged supervisor."""
    import threading, time as _t
    release = threading.Event()
    cat = vs.GatewayCatalog(lambda: (release.wait(30), GATEWAY_MODELS)[1], ttl_s=300)
    t0 = _t.perf_counter()
    snap = cat.snapshot(settle_s=0.3)
    elapsed = _t.perf_counter() - t0
    assert snap["ids"] == [] and 0.25 < elapsed < 3.0
    release.set()


def test_settling_never_waits_twice_for_a_stale_refresh():
    """Only the FIRST listing is worth waiting for — after that the last good list is
    already an answer, so a background refresh must not stall a Save."""
    import threading, time as _t
    clock = _Clock()
    gate = threading.Event()
    calls = []

    def _list():
        calls.append(1)
        if len(calls) > 1:
            gate.wait(30)
        return GATEWAY_MODELS

    cat = vs.GatewayCatalog(_list, ttl_s=10, clock=clock)
    assert cat.snapshot(settle_s=5)["ids"] == GATEWAY_MODELS
    clock.t += 60                                     # the cache is stale now
    t0 = _t.perf_counter()
    snap = cat.snapshot(settle_s=5)
    assert (_t.perf_counter() - t0) < 1.0, "a stale refresh must not block the caller"
    assert snap["ids"] == GATEWAY_MODELS
    gate.set()
