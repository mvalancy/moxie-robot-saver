"""
Full-stack assembly tests (M7) — config.build_synthesizer/build_transcriber + the
run.assemble() wiring that installs the brain + optional STT + optional voice on the
runtime. Pure (no broker/voice creds): STT skips without faster-whisper, voice skips
without MOXIE_VOICE_BASE_URL.
"""
import importlib
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MQTT = os.path.join(REPO, "mqtt")
sys.path.insert(0, MQTT)
sys.path.insert(0, os.path.join(MQTT, "supervisor"))


def _fresh_config(env):
    """Import config with a controlled environment (echo app, no voice/whisper).

    `MOXIE_SKIP_DOTENV` is what makes the popping below MEAN anything. `config._load_env`
    reads `mqtt/.env` with `setdefault` at import, so on a machine that has a real one —
    every developer's; never CI's, never a worktree's, because the file is git-ignored —
    a reload put back every variable this function just deleted, and these tests asserted
    nothing (orchestration playbook rule 20). The flag is checked before the file is
    opened, so "unset" is unset. See `test_config_dotenv.py`.
    """
    os.environ["MOXIE_SKIP_DOTENV"] = "1"
    for k in ("MOXIE_APP", "MOXIE_VOICE_BASE_URL", "MOXIE_STT",
              "MOXIE_LLM_API_KEY", "MOXIE_LLM_BASE_URL",
              "MOXIE_VOICE_MODEL", "MOXIE_VOICE_FORMAT", "MOXIE_VOICE_SAMPLE_RATE",
              "MOXIE_TTS_VOICE"):
        os.environ.pop(k, None)
    os.environ.update(env)
    # config caches module-level values at import — reload for a clean read
    import config as _c
    return importlib.reload(_c)


def test_build_synthesizer_none_without_voice_url():
    c = _fresh_config({"MOXIE_APP": "echo", "MOXIE_STT": "off"})
    assert c.build_synthesizer() is None            # no voice url, no piper model → off


def test_build_synthesizer_tone_engine():
    # MOXIE_TTS=tone → the built-in zero-dep voice (no server, no piper model)
    os.environ["MOXIE_TTS"] = "tone"
    try:
        c = _fresh_config({"MOXIE_APP": "echo", "MOXIE_STT": "off"})
        synth = c.build_synthesizer()
        assert synth is not None and synth.name == "tone"
        assert synth.synthesize("hi")            # actually produces audio bytes
    finally:
        os.environ.pop("MOXIE_TTS", None)


def test_build_synthesizer_tts_off_forces_none():
    os.environ["MOXIE_TTS"] = "off"
    os.environ["MOXIE_PIPER_MODEL"] = "/models/amy.onnx"   # even with a model configured
    try:
        c = _fresh_config({"MOXIE_APP": "echo", "MOXIE_STT": "off"})
        assert c.build_synthesizer() is None
    finally:
        os.environ.pop("MOXIE_TTS", None)
        os.environ.pop("MOXIE_PIPER_MODEL", None)


def test_build_synthesizer_piper_model_when_piper_absent_is_none():
    # MOXIE_PIPER_MODEL set but piper isn't installed (CI) → clean None, no raise
    os.environ["MOXIE_PIPER_MODEL"] = "/models/en_US-amy-medium.onnx"
    try:
        c = _fresh_config({"MOXIE_APP": "echo", "MOXIE_STT": "off"})
        from moxie_sdk.tts import PiperSynthesizer
        if not PiperSynthesizer.available():
            assert c.build_synthesizer() is None
    finally:
        os.environ.pop("MOXIE_PIPER_MODEL", None)


def test_build_transcriber_off_is_none():
    c = _fresh_config({"MOXIE_APP": "echo", "MOXIE_STT": "off"})
    assert c.build_transcriber() is None


def test_build_transcriber_auto_is_none_without_whisper():
    c = _fresh_config({"MOXIE_APP": "echo", "MOXIE_STT": "auto"})
    # "auto" yields None only when faster-whisper isn't installed (CI); with it installed
    # (a dev box with the voice extras) auto legitimately builds a transcriber.
    from moxie_sdk.stt import WhisperTranscriber
    if not WhisperTranscriber.available():
        assert c.build_transcriber() is None
    else:
        assert c.build_transcriber() is not None


def test_assemble_builds_runtime_with_configured_app():
    c = _fresh_config({"MOXIE_APP": "echo", "MOXIE_STT": "off"})
    import run
    importlib.reload(run)
    rt = run.assemble(c)
    assert rt.app.name == "echo"
    assert rt._synth is None and rt._transcriber is None   # nothing configured → off
    assert rt.child.nickname == c.CHILD_NICKNAME


# --- the gateway voice: one env var, plus a standby behind it ----------------
# `MOXIE_VOICE_BASE_URL` is the whole switch (live on our LiteLLM gateway since
# 2026-09-02). These stay hermetic by swapping the constructor the config calls, so they
# run in the openai-less venv too — the real endpoint is exercised in
# sim/tests/test_live_gateway_tts.py.

def _stub_voice(monkeypatch, calls):
    """Replace moxie_sdk.tts.make_voice_synthesizer with a recorder + a dummy engine."""
    import moxie_sdk.tts as tts

    class _Stub(tts.Synthesizer):
        name = "openai-voice"
        sample_rate = 22050

        def synthesize(self, text, voice=None):
            return b"\x00\x01"

    def _fake(base_url, api_key, voice=None, **kw):
        calls.append(dict(base_url=base_url, api_key=api_key, voice=voice, **kw))
        return _Stub() if base_url else None

    monkeypatch.setattr(tts, "make_voice_synthesizer", _fake)


def test_voice_url_is_the_whole_switch_with_defaults(monkeypatch):
    """One variable turns the gateway voice on, and the documented defaults ride along:
    model `piper-amy`, `wav` (whose header carries the true rate), 22050 for pcm, and an
    empty `voice` so the client derives it from the model."""
    calls = []
    _stub_voice(monkeypatch, calls)
    c = _fresh_config({"MOXIE_APP": "echo", "MOXIE_STT": "off",
                       "MOXIE_VOICE_BASE_URL": "https://gateway.graphlings.net/v1"})
    synth = c.build_synthesizer()
    assert c.VOICE_MODEL == "piper-amy" and c.VOICE_FORMAT == "wav"
    assert c.VOICE_SAMPLE_RATE == 22050 and c.TTS_VOICE == ""
    assert calls == [{"base_url": "https://gateway.graphlings.net/v1",
                      "api_key": c.VOICE_API_KEY, "voice": "",
                      "model": "piper-amy", "response_format": "wav",
                      "sample_rate": 22050}]
    from moxie_sdk.tts import FallbackSynthesizer
    assert isinstance(synth, FallbackSynthesizer)
    assert synth.voice_name == "openai-voice"      # the gateway speaks…
    assert synth._standby.name == "tone"           # …with the tone as its standby


def test_voice_knobs_are_all_honored(monkeypatch):
    calls = []
    _stub_voice(monkeypatch, calls)
    for k in ("MOXIE_VOICE_MODEL", "MOXIE_VOICE_FORMAT", "MOXIE_VOICE_SAMPLE_RATE",
              "MOXIE_TTS_VOICE"):
        os.environ.pop(k, None)
    try:
        c = _fresh_config({"MOXIE_APP": "echo", "MOXIE_STT": "off",
                           "MOXIE_VOICE_BASE_URL": "http://voice.local/v1",
                           "MOXIE_VOICE_MODEL": "piper-ryan",
                           "MOXIE_VOICE_FORMAT": "PCM",       # case-folded
                           "MOXIE_VOICE_SAMPLE_RATE": "16000",
                           "MOXIE_TTS_VOICE": "nova"})
        c.build_synthesizer()
        assert calls[0]["model"] == "piper-ryan" and calls[0]["response_format"] == "pcm"
        assert calls[0]["sample_rate"] == 16000 and calls[0]["voice"] == "nova"
        # a junk sample rate must not crash the supervisor at import
        c2 = _fresh_config({"MOXIE_APP": "echo", "MOXIE_STT": "off",
                            "MOXIE_VOICE_SAMPLE_RATE": "not-a-number"})
        assert c2.VOICE_SAMPLE_RATE == 22050
    finally:
        for k in ("MOXIE_VOICE_MODEL", "MOXIE_VOICE_FORMAT", "MOXIE_VOICE_SAMPLE_RATE",
                  "MOXIE_TTS_VOICE"):
            os.environ.pop(k, None)


def test_voice_precedence_and_the_piper_standby(monkeypatch):
    """Precedence is unchanged — voice server > Piper > tone — and the rung the voice
    server displaced becomes its standby, so a gateway failure lands on Piper (not on
    silence) when a Piper model is configured."""
    calls = []
    _stub_voice(monkeypatch, calls)
    import moxie_sdk.tts as tts
    monkeypatch.setattr(tts, "make_piper_synthesizer",
                        lambda *a, **kw: tts.PiperSynthesizer(
                            "amy.onnx", voice_fn=lambda t: b"\x00" * 8))
    c = _fresh_config({"MOXIE_APP": "echo", "MOXIE_STT": "off",
                       "MOXIE_VOICE_BASE_URL": "http://voice.local/v1"})
    synth = c.build_synthesizer()
    assert synth.voice_name == "openai-voice" and synth._standby.name == "piper"
    # …and with no voice URL the same Piper is the voice itself (unchanged behavior)
    c = _fresh_config({"MOXIE_APP": "echo", "MOXIE_STT": "off"})
    assert c.build_synthesizer().name == "piper"


def test_tts_off_still_beats_a_voice_url(monkeypatch):
    calls = []
    _stub_voice(monkeypatch, calls)
    os.environ["MOXIE_TTS"] = "off"
    try:
        c = _fresh_config({"MOXIE_APP": "echo", "MOXIE_STT": "off",
                           "MOXIE_VOICE_BASE_URL": "http://voice.local/v1"})
        assert c.build_synthesizer() is None and calls == []
    finally:
        os.environ.pop("MOXIE_TTS", None)
