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
    """Import config with a controlled environment (echo app, no voice/whisper)."""
    for k in ("MOXIE_APP", "MOXIE_VOICE_BASE_URL", "MOXIE_STT",
              "MOXIE_LLM_API_KEY", "MOXIE_LLM_BASE_URL"):
        os.environ.pop(k, None)
    os.environ.update(env)
    # config caches module-level values at import — reload for a clean read
    import config as _c
    return importlib.reload(_c)


def test_build_synthesizer_none_without_voice_url():
    c = _fresh_config({"MOXIE_APP": "echo", "MOXIE_STT": "off"})
    assert c.build_synthesizer() is None            # no voice url, no piper model → off


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
    # faster-whisper isn't installed in CI/test → auto yields None (text turns still work)
    assert c.build_transcriber() is None


def test_assemble_builds_runtime_with_configured_app():
    c = _fresh_config({"MOXIE_APP": "echo", "MOXIE_STT": "off"})
    import run
    importlib.reload(run)
    rt = run.assemble(c)
    assert rt.app.name == "echo"
    assert rt._synth is None and rt._transcriber is None   # nothing configured → off
    assert rt.child.nickname == c.CHILD_NICKNAME
