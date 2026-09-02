"""
Gateway EARS — the hermetic tier for `moxie_sdk/stt.py`'s cloud transcriber and for the
`MOXIE_STT` switch that chooses between it and local whisper.

`test_stt.py` covers the VAD accumulator and the wire encoder; this file covers what was
added when the gateway's `/v1/audio/transcriptions` went live (2026-09-02): the in-memory
WAV wrapping (the robot's mic is headerless 16 kHz PCM and the endpoint wants a *file*),
the request shape, the retry/backoff seam, the latching fallback, and the config
precedence — including the one the deployment story depends on, that
**`MOXIE_STT=whisper` keeps the ears local even when a gateway URL is configured**.

Everything here runs with **no `openai` installed** (playbook rule 9): the transcriber
takes a `client=` fake, the config tests stub `make_openai_transcriber`, and the only
network in the file is imaginary. The real endpoint is exercised in
`sim/tests/test_live_gateway_stt.py`.
"""
import importlib
import io
import os
import sys
import wave

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MQTT = os.path.join(REPO, "mqtt")
sys.path.insert(0, MQTT)
sys.path.insert(0, os.path.join(MQTT, "supervisor"))

from moxie_sdk.stt import (  # noqa: E402
    FallbackTranscriber, NullTranscriber, OpenAITranscriber, SttServerError,
    SttSession, Transcriber, VADState, WhisperTranscriber, make_openai_transcriber,
    transcript_text, wav_bytes,
)

#: 0.5 s of (silent but long enough) 16 kHz mono PCM16 — over the min-length gate.
PCM_16K = b"\x11\x22" * 8000


class _Reply:
    """What the OpenAI SDK hands back from `audio.transcriptions.create`."""

    def __init__(self, text):
        self.text = text


class _FakeClient:
    """Records every `/audio/transcriptions` call; replays a scripted list of results
    (an Exception instance is raised, anything else is returned)."""

    def __init__(self, *results):
        self.calls = []
        self._results = list(results) or [_Reply("hello moxie")]
        self.audio = self                       # client.audio.transcriptions.create(…)

    @property
    def transcriptions(self):
        return self

    def create(self, **kw):
        # The file tuple carries a live stream; read it HERE, as the SDK would, so a
        # test can prove a retry re-uploaded real bytes instead of an exhausted buffer.
        name, stream, mime = kw["file"]
        kw = dict(kw, file=(name, stream.read(), mime))
        self.calls.append(kw)
        result = self._results.pop(0) if len(self._results) > 1 else self._results[0]
        if isinstance(result, Exception):
            raise result
        return result


def _transcriber(client, **kw):
    """An `OpenAITranscriber` on a fake client with an instant backoff."""
    from moxie_sdk.chat import Pacer
    slept = kw.pop("slept", [])
    kw.setdefault("pacer", Pacer(sleep=lambda s: slept.append(s)))
    kw.setdefault("sleep", lambda s: slept.append(s))
    return OpenAITranscriber("https://gateway.example/v1", "sk-test", client=client, **kw)


# ------------------------------------------------------------------ the WAV --
def test_pcm_is_wrapped_in_a_readable_riff_wav_at_the_given_rate():
    """The endpoint takes a FILE; the robot sends headerless frames. Parse the header
    back rather than trusting the writer."""
    raw = wav_bytes(PCM_16K, 16000)
    assert raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"
    with wave.open(io.BytesIO(raw), "rb") as w:
        assert w.getframerate() == 16000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getnframes() == len(PCM_16K) // 2
        assert w.readframes(w.getnframes()) == PCM_16K


def test_the_header_carries_the_true_rate_not_a_constant():
    """A WAV that lied about its rate would pitch-shift the audio and wreck the
    transcript — so 22050 Hz TTS audio must arrive labelled 22050."""
    for rate in (8000, 16000, 22050, 24000, 48000):
        with wave.open(io.BytesIO(wav_bytes(PCM_16K, rate)), "rb") as w:
            assert w.getframerate() == rate
    assert wav_bytes(b"", 16000)[:4] == b"RIFF"        # empty PCM still makes a file


# -------------------------------------------------------------- the request --
def test_the_request_carries_the_model_and_a_wav_file_tuple():
    client = _FakeClient(_Reply("  hi there  "))
    t = _transcriber(client, model="stt-whisper")
    assert t.transcribe(PCM_16K, 16000) == "hi there"          # .text is stripped

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "stt-whisper"
    assert call["response_format"] == "json"
    name, blob, mime = call["file"]
    assert name.endswith(".wav") and mime == "audio/wav"
    with wave.open(io.BytesIO(blob), "rb") as w:               # a real WAV went up
        assert w.getframerate() == 16000 and w.readframes(w.getnframes()) == PCM_16K


def test_the_wav_rate_follows_the_audio_the_runtime_hands_over():
    """`SttSession` passes its own sample rate through; 22050 audio must not be
    uploaded as 16000."""
    client = _FakeClient(_Reply("ok"))
    _transcriber(client).transcribe(PCM_16K, 22050)
    with wave.open(io.BytesIO(client.calls[0]["file"][1]), "rb") as w:
        assert w.getframerate() == 22050


def test_the_model_is_public_and_named_in_describe():
    t = _transcriber(_FakeClient(), model="graphling-stt")
    assert t.model == "graphling-stt"                 # a console model picker reads this
    assert t.describe() == "openai-stt (graphling-stt)"
    assert t.name == "openai-stt"


def test_empty_or_too_short_audio_never_costs_a_request():
    client = _FakeClient(_Reply("should not happen"))
    t = _transcriber(client)
    assert t.transcribe(b"", 16000) == ""
    assert t.transcribe(b"\x00\x00" * 100, 16000) == ""        # 12 ms — a door slam
    assert client.calls == []
    # …and the gate is a DURATION, not a byte count: the very same buffer at a low
    # sample rate is half a second of audio, and that one does go up.
    assert t.transcribe(b"\x00\x00" * 100, 400) == "should not happen"
    assert len(client.calls) == 1


def test_a_missing_text_field_is_an_error_not_a_silent_empty_turn():
    with pytest.raises(SttServerError):
        transcript_text(object())
    assert transcript_text({"text": " hi "}) == "hi"           # dict reply
    assert transcript_text("plain") == "plain"                 # bare string reply


# ----------------------------------------------------------- retry + pacing --
class _RateLimited(Exception):
    status_code = 429


class _Boom(Exception):
    """A 400 — an unknown model name. Never transient, never retried."""
    status_code = 400


def test_a_429_backs_off_and_then_succeeds():
    slept = []
    client = _FakeClient(_RateLimited("slow down"), _Reply("hello moxie"))
    t = _transcriber(client, slept=slept)
    assert t.transcribe(PCM_16K, 16000) == "hello moxie"
    assert len(client.calls) == 2, "the 429 was not retried"
    assert slept and slept[0] > 0, "no backoff was taken"
    # the retry uploaded the audio AGAIN — not an exhausted stream
    assert client.calls[1]["file"][1] == client.calls[0]["file"][1]
    assert len(client.calls[1]["file"][1]) > 44


def test_a_hard_error_is_raised_after_no_retries():
    client = _FakeClient(_Boom("Invalid model name"))
    with pytest.raises(_Boom):
        _transcriber(client).transcribe(PCM_16K, 16000)
    assert len(client.calls) == 1, "a 400 must not be retried"


# ------------------------------------------------------------- the fallback --
class _Deaf(Transcriber):
    name = "deaf"

    def transcribe(self, pcm, sample_rate=16000):
        raise RuntimeError("gateway is down")


class _Local(Transcriber):
    name = "local-fake"

    def __init__(self):
        self.calls = 0

    def transcribe(self, pcm, sample_rate=16000):
        self.calls += 1
        return "heard locally"


def test_the_fallback_latches_to_the_standby_and_reports_once():
    logged, standby = [], _Local()
    fb = FallbackTranscriber(_Deaf(), standby, log=logged.append)
    assert fb.describe() == "deaf (standby: local-fake)"

    assert fb.transcribe(PCM_16K) == "heard locally"      # first failure downgrades
    assert fb.failed and fb.engine_name == "local-fake"
    assert fb.transcribe(PCM_16K) == "heard locally"
    assert fb.transcribe(PCM_16K) == "heard locally"
    assert standby.calls == 3, "the standby did not take over the whole run"
    assert len(logged) == 1, f"the failure was reported {len(logged)} times: {logged}"
    assert "deaf failed" in logged[0] and "local-fake" in logged[0]
    assert fb.describe() == "local-fake (standby — deaf failed)"


def test_a_healthy_primary_is_never_downgraded():
    primary, standby = _Local(), _Local()
    fb = FallbackTranscriber(primary, standby)
    assert fb.transcribe(PCM_16K) == "heard locally"
    assert not fb.failed and primary.calls == 1 and standby.calls == 0


def test_without_local_whisper_the_standby_is_an_honest_empty_transcript():
    """A box with no whisper wheels still must not raise mid-turn — it hears nothing and
    says so, which is what the runtime already does with an empty utterance."""
    fb = FallbackTranscriber(_Deaf(), NullTranscriber(), log=lambda m: None)
    assert fb.transcribe(PCM_16K) == ""
    assert fb.failed and fb.engine_name == "no-ears"


def test_the_fallback_is_a_drop_in_transcriber_for_the_runtime_session():
    """`SttSession` (and therefore `MoxieRuntime.feed_stt`) takes it unchanged."""
    session = SttSession(FallbackTranscriber(_Deaf(), _Local(), log=lambda m: None))
    assert session.feed(VADState.START_OF_SPEECH, PCM_16K) is None
    assert session.feed(VADState.END_OF_SPEECH, b"") == "heard locally"


def test_availability_needs_both_the_sdk_and_an_endpoint():
    assert OpenAITranscriber.available("") is False        # no endpoint → unavailable
    try:
        import openai  # noqa: F401
        assert OpenAITranscriber.available("https://gateway.example/v1") is True
    except ImportError:
        assert OpenAITranscriber.available("https://gateway.example/v1") is False
    assert isinstance(WhisperTranscriber.available(), bool)
    assert make_openai_transcriber("", "k") is None


# ------------------------------------------------------- the MOXIE_STT knob --
_STT_ENV = ("MOXIE_STT", "MOXIE_STT_MODEL", "MOXIE_STT_BASE_URL", "MOXIE_STT_API_KEY",
            "MOXIE_VOICE_BASE_URL", "MOXIE_VOICE_API_KEY", "MOXIE_LLM_API_KEY",
            "MOXIE_LLM_BASE_URL", "MOXIE_TTS", "MOXIE_PIPER_MODEL", "MOXIE_APP")


def _fresh_config(monkeypatch, **env):
    """`mqtt/config.py` re-imported with a controlled environment (the pattern
    `test_assemble.py` uses for the voice knobs)."""
    for k in _STT_ENV:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import config as _c
    return importlib.reload(_c)


def _stub_engines(monkeypatch, *, whisper=True, openai_sdk=True):
    """Swap both engines for recorders, so the precedence tests need neither
    faster-whisper nor openai (and never load a model)."""
    import moxie_sdk.stt as stt
    built = {"whisper": [], "gateway": []}

    class _FakeWhisper(stt.Transcriber):
        name = "faster-whisper"

        def __init__(self, model="base.en", **kw):
            self.model = model
            built["whisper"].append(model)

        def describe(self):
            return f"{self.name} ({self.model})"

        def transcribe(self, pcm, sample_rate=16000):
            return "local"

        @classmethod
        def available(cls):
            return whisper

    class _FakeGateway(stt.Transcriber):
        name = "openai-stt"

        def __init__(self, model="stt-whisper"):
            self.model = model

        def describe(self):
            return f"{self.name} ({self.model})"

        def transcribe(self, pcm, sample_rate=16000):
            return "cloud"

    def _make(base_url, api_key, model="stt-whisper", **kw):
        if not base_url:
            return None
        built["gateway"].append({"base_url": base_url, "api_key": api_key,
                                 "model": model})
        return _FakeGateway(model)

    monkeypatch.setattr(stt, "WhisperTranscriber", _FakeWhisper)
    monkeypatch.setattr(stt, "make_openai_transcriber", _make)
    monkeypatch.setattr(stt.OpenAITranscriber, "available",
                        classmethod(lambda cls, base_url="": bool(base_url)
                                    and openai_sdk))
    return built


GW = "https://gateway.graphlings.net/v1"


def test_auto_with_a_configured_gateway_hears_in_the_cloud(monkeypatch):
    built = _stub_engines(monkeypatch)
    c = _fresh_config(monkeypatch, MOXIE_STT="auto", MOXIE_VOICE_BASE_URL=GW,
                      MOXIE_LLM_API_KEY="sk-test")
    t = c.build_transcriber()
    assert isinstance(t, FallbackTranscriber)
    assert t.engine_name == "openai-stt"                       # the cloud hears…
    assert t._standby.name == "faster-whisper"                 # …whisper stands by
    assert t.describe() == "openai-stt (stt-whisper) (standby: faster-whisper (base.en))"
    # one gateway, one key: the STT endpoint defaulted to the VOICE url + the LLM key
    assert built["gateway"] == [{"base_url": GW, "api_key": "sk-test",
                                 "model": "stt-whisper"}]
    # the standby runs the LOCAL default, never a gateway model name
    assert built["whisper"] == ["base.en"]


def test_auto_without_a_key_stays_local_which_is_todays_behaviour(monkeypatch):
    """`STT_BASE_URL` falls back to `LLM_BASE_URL`, which has a default — so a URL alone
    is never evidence anyone meant the cloud. No key → local whisper, exactly as before
    this knob existed (this is what keeps the SIL smoke and CI unchanged)."""
    built = _stub_engines(monkeypatch)
    c = _fresh_config(monkeypatch, MOXIE_STT="auto")
    t = c.build_transcriber()
    assert t.name == "faster-whisper" and built["gateway"] == []
    assert built["whisper"] == ["base.en"]


def test_local_whisper_is_selectable_even_with_a_gateway_configured(monkeypatch):
    """The deployment promise: a home appliance keeps a child's voice in the house. Both
    spellings force the local engine over a fully configured gateway."""
    for value in ("whisper", "local", "WHISPER"):
        built = _stub_engines(monkeypatch)
        c = _fresh_config(monkeypatch, MOXIE_STT=value, MOXIE_STT_BASE_URL=GW,
                          MOXIE_VOICE_BASE_URL=GW, MOXIE_STT_API_KEY="sk-test")
        t = c.build_transcriber()
        assert t.name == "faster-whisper", f"MOXIE_STT={value} left the ears in the cloud"
        assert built["gateway"] == [], f"MOXIE_STT={value} still built a gateway client"


def test_local_piper_is_selectable_the_same_way_for_the_voice(monkeypatch):
    """The symmetric statement for TTS, asserted rather than assumed: with no voice URL a
    configured Piper IS the voice (auto precedence voice-server → Piper → tone), and —
    the owner rule — an explicit `MOXIE_TTS=piper` keeps the voice local even with a
    gateway fully configured, exactly like `MOXIE_STT=whisper` keeps the ears local."""
    import moxie_sdk.tts as tts
    monkeypatch.setattr(tts, "make_piper_synthesizer",
                        lambda *a, **kw: tts.PiperSynthesizer(
                            "amy.onnx", voice_fn=lambda t: b"\x00" * 8))
    c = _fresh_config(monkeypatch, MOXIE_STT="off",
                      MOXIE_PIPER_MODEL="/models/en_US-amy-medium.onnx")
    assert c.build_synthesizer().name == "piper"
    c = _fresh_config(monkeypatch, MOXIE_STT="off", MOXIE_TTS="piper",
                      MOXIE_PIPER_MODEL="/models/en_US-amy-medium.onnx",
                      MOXIE_VOICE_BASE_URL=GW, MOXIE_LLM_API_KEY="sk-test-not-a-real-key-0000")
    assert c.build_synthesizer().name == "piper", "MOXIE_TTS=piper let the gateway win"
    import pytest
    c = _fresh_config(monkeypatch, MOXIE_STT="off", MOXIE_TTS="gateway")
    with pytest.raises(SystemExit):
        c.build_synthesizer()                       # explicit gateway with no URL exits loudly


def test_gateway_is_forced_even_when_whisper_is_installed(monkeypatch):
    built = _stub_engines(monkeypatch)
    c = _fresh_config(monkeypatch, MOXIE_STT="gateway", MOXIE_STT_BASE_URL=GW)
    t = c.build_transcriber()
    assert t.engine_name == "openai-stt"
    # …and it is forced even with NO key (an explicit choice is not second-guessed)
    assert built["gateway"] == [{"base_url": GW, "api_key": "", "model": "stt-whisper"}]


def test_gateway_without_the_sdk_fails_loudly_instead_of_hearing_elsewhere(monkeypatch):
    _stub_engines(monkeypatch, openai_sdk=False)
    c = _fresh_config(monkeypatch, MOXIE_STT="gateway", MOXIE_STT_BASE_URL=GW)
    with pytest.raises(SystemExit) as e:
        c.build_transcriber()
    assert "openai" in str(e.value)


def test_whisper_without_the_wheels_fails_loudly_too(monkeypatch):
    _stub_engines(monkeypatch, whisper=False)
    c = _fresh_config(monkeypatch, MOXIE_STT="whisper")
    with pytest.raises(SystemExit) as e:
        c.build_transcriber()
    assert "faster-whisper" in str(e.value)


def test_off_beats_everything(monkeypatch):
    built = _stub_engines(monkeypatch)
    c = _fresh_config(monkeypatch, MOXIE_STT="off", MOXIE_STT_BASE_URL=GW,
                      MOXIE_STT_API_KEY="sk-test")
    assert c.build_transcriber() is None
    assert built["gateway"] == [] and built["whisper"] == []


def test_the_model_knob_names_a_model_on_whichever_engine_runs(monkeypatch):
    built = _stub_engines(monkeypatch)
    c = _fresh_config(monkeypatch, MOXIE_STT="gateway", MOXIE_STT_BASE_URL=GW,
                      MOXIE_STT_MODEL="graphling-stt")
    assert c.build_transcriber().engine.model == "graphling-stt"
    assert built["gateway"][0]["model"] == "graphling-stt"

    built = _stub_engines(monkeypatch)
    c = _fresh_config(monkeypatch, MOXIE_STT="whisper", MOXIE_STT_MODEL="small.en")
    assert c.build_transcriber().model == "small.en"


def test_the_endpoint_and_key_fall_back_voice_then_llm(monkeypatch):
    built = _stub_engines(monkeypatch)
    c = _fresh_config(monkeypatch, MOXIE_STT="gateway",
                      MOXIE_STT_BASE_URL="https://ears.example/v1",
                      MOXIE_STT_API_KEY="sk-ears", MOXIE_VOICE_BASE_URL=GW,
                      MOXIE_LLM_API_KEY="sk-llm")
    c.build_transcriber()                       # explicit STT vars win
    assert built["gateway"][0] == {"base_url": "https://ears.example/v1",
                                   "api_key": "sk-ears", "model": "stt-whisper"}

    built = _stub_engines(monkeypatch)
    c = _fresh_config(monkeypatch, MOXIE_STT="gateway", MOXIE_VOICE_BASE_URL=GW,
                      MOXIE_VOICE_API_KEY="sk-voice", MOXIE_LLM_API_KEY="sk-llm")
    c.build_transcriber()                       # then the voice endpoint + its key
    assert built["gateway"][0] == {"base_url": GW, "api_key": "sk-voice",
                                   "model": "stt-whisper"}

    built = _stub_engines(monkeypatch)
    c = _fresh_config(monkeypatch, MOXIE_STT="gateway",
                      MOXIE_LLM_BASE_URL="https://brain.example/v1",
                      MOXIE_LLM_API_KEY="sk-llm")
    c.build_transcriber()                       # finally the brain's
    assert built["gateway"][0] == {"base_url": "https://brain.example/v1",
                                   "api_key": "sk-llm", "model": "stt-whisper"}


def test_nothing_set_still_returns_what_it_returned_before(monkeypatch):
    """The no-regression pin: an unset environment builds local whisper when it is
    installed and None when it is not — the M3 contract, unchanged."""
    for k in _STT_ENV:
        monkeypatch.delenv(k, raising=False)
    import config as _c
    c = importlib.reload(_c)
    t = c.build_transcriber()
    if WhisperTranscriber.available():
        assert t is not None and t.name == "faster-whisper"
    else:
        assert t is None


def test_the_startup_log_line_says_which_ears_are_listening(monkeypatch):
    """`run.assemble` reports `describe()`, so a parent reading the log sees the engine,
    the model and the standby rather than the wrapper's own name."""
    _stub_engines(monkeypatch)
    c = _fresh_config(monkeypatch, MOXIE_STT="gateway", MOXIE_STT_BASE_URL=GW,
                      MOXIE_APP="echo")
    # By file path, not `import run`: the console has a `server/run.py` too, and which
    # one a bare import finds depends on what an earlier test put on sys.path.
    import importlib.util
    spec = importlib.util.spec_from_file_location("_supervisor_run",
                                                  os.path.join(MQTT, "run.py"))
    run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run)
    rt = run.assemble(c)
    assert rt._transcriber.describe().startswith("openai-stt (stt-whisper)")


# --------------------------------------------- which gateway models are audio --
# Groundwork for a console model picker: `GET /v1/models` returns one flat list with
# nothing marking a model as audio, so the names are the only contract. The golden list
# below is exactly what the gateway served on 2026-09-02 (six voices, three ears, and
# chat models that must not leak into either).

GATEWAY_MODELS = [
    "piper-amy", "piper-ryan", "graphling-tts-narrator", "graphling-tts-character",
    "stt-whisper", "graphling-stt", "tts-piper-amy", "tts-piper-ryan",
    "stt-whisper-base", "graphling-medium", "graphling-small", "qwen2.5-7b",
]


def test_classify_audio_models_golden_gateway_list():
    from moxie_sdk.audio_models import classify_audio_models
    assert classify_audio_models(GATEWAY_MODELS) == {
        "tts": ["piper-amy", "piper-ryan", "graphling-tts-narrator",
                "graphling-tts-character", "tts-piper-amy", "tts-piper-ryan"],
        "stt": ["stt-whisper", "graphling-stt", "stt-whisper-base"],
    }


def test_classify_keeps_input_order_and_drops_everything_else():
    """A picker whose entries shuffle between page loads is a bug report."""
    from moxie_sdk.audio_models import classify_audio_models
    got = classify_audio_models(["qwen2.5-7b", "piper-ryan", "", "stt-whisper",
                                 "piper-amy", None, "text-embedding-3-small"])
    assert got == {"tts": ["piper-ryan", "piper-amy"], "stt": ["stt-whisper"]}
    assert classify_audio_models([]) == {"tts": [], "stt": []}


def test_default_models_prefer_the_ones_moxie_ships_with():
    from moxie_sdk.audio_models import default_stt_model, default_tts_model
    assert default_tts_model(GATEWAY_MODELS) == "piper-amy"
    assert default_stt_model(GATEWAY_MODELS) == "stt-whisper"
    # …and fall back to whatever the gateway does offer
    assert default_tts_model(["graphling-tts-narrator", "graphling-medium"]) == \
        "graphling-tts-narrator"
    assert default_stt_model(["graphling-stt", "graphling-medium"]) == "graphling-stt"
    # …or say honestly that this gateway cannot speak / cannot hear
    assert default_tts_model(["graphling-medium"]) is None
    assert default_stt_model(["graphling-medium"]) is None
