"""
Live GATEWAY voice — real speech out of our LiteLLM proxy, read back by Whisper.

`test_tts.py` proves the *shape* of the gateway client against a fake `/audio/speech`
(WAV unwrapping, the derived rate, the required-but-ignored `voice` field, the standby).
Every one of those tests would still pass if the gateway returned four seconds of hiss.
This file is the one that cannot: **the audio must transcribe back into the sentence we
asked for**, at word-overlap ≥ 0.7, and a companion guard shows the same pipeline
*rejects* the built-in `ToneSynthesizer` placeholder — so a green here means real speech
came down the wire, not that the plumbing is connected.

Exactly the tier-1 pattern of `test_live_talk_e2e.py` (same floor, same anti-tone guard,
same `[gw]` printed evidence), pointed at the gateway instead of at local Piper.

**Budget: 4 requests to `/v1/audio/speech`, one per test** — one WAV (Amy), one WAV
(Ryan, to show the model switch), one PCM, one deliberately-unknown model to prove the
downgrade. The synthesizer is built by `config.build_synthesizer()` rather than by hand,
so what is under test is the shipped switch (`MOXIE_VOICE_BASE_URL` + the model), not a
test-local client.

Runs when `MOXIE_VOICE_BASE_URL` and a key are set (mqtt/.env of this tree or the main
checkout); skips cleanly and instantly otherwise, so the hermetic tier is unaffected.

    MOXIE_VOICE_BASE_URL=https://gateway.graphlings.net/v1 \
      .venv/bin/python -m pytest sim/tests/test_live_gateway_tts.py -q -s
"""
import importlib
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.dirname(__file__))

pytest.importorskip("openai", reason="openai SDK not installed (live gateway test)")
pytest.importorskip("faster_whisper", reason="faster-whisper not installed (live STT)")
pytest.importorskip("numpy")

import helpers_audio as A                                    # noqa: E402
from helpers_runtime import load_repo_dotenv                 # noqa: E402

load_repo_dotenv()          # mqtt/.env from this tree or the main checkout

BASE = (os.environ.get("MOXIE_VOICE_BASE_URL") or "").strip()
KEY = (os.environ.get("MOXIE_VOICE_API_KEY")
       or os.environ.get("MOXIE_LLM_API_KEY")
       or os.environ.get("LITELLM_MASTER_KEY") or "")
MODEL = (os.environ.get("MOXIE_VOICE_MODEL") or "piper-amy").strip()
#: The second registered voice. The point of the switch test is that the MODEL NAME
#: chooses the voice, so this must differ from MODEL.
ALT_MODEL = os.environ.get("MOXIE_VOICE_ALT_MODEL", "piper-ryan").strip()

pytestmark = pytest.mark.skipif(
    not (BASE and KEY),
    reason="no gateway voice configured (set MOXIE_VOICE_BASE_URL + a key in mqtt/.env)")

#: Fixed, ordinary, child-facing English — no proper nouns Whisper has never heard,
#: because the assertion is about OUR audio, not about ASR vocabulary. Same line the
#: local-Piper tier speaks, so the two runs are directly comparable.
MOXIE_LINE = "Hi Sam, I am Moxie. Do you want to hear a story about a brave little robot?"

STT_FLOOR = 0.7          # Amy through the gateway, recovered by Whisper
ALT_FLOOR = 0.6          # a second voice: same floor spirit, one notch of slack

_CACHE = {}


def _whisper():
    from moxie_sdk.stt import WhisperTranscriber
    if "whisper" not in _CACHE:
        with A.Stage("load") as s:
            _CACHE["whisper"] = WhisperTranscriber("base.en", device="cpu",
                                                   compute_type="int8")
        print(f"\n[gw] loaded faster-whisper base.en (int8/cpu) in {s.seconds:.2f}s")
    return _CACHE["whisper"]


def _config(**env):
    """Import `mqtt/config.py` with a controlled environment — the shipped switch, not a
    hand-rolled client. Returns the reloaded module."""
    keep = {k: os.environ.get(k) for k in
            ("MOXIE_VOICE_MODEL", "MOXIE_VOICE_FORMAT", "MOXIE_VOICE_SAMPLE_RATE",
             "MOXIE_TTS", "MOXIE_PIPER_MODEL")}
    for k in keep:
        os.environ.pop(k, None)
    os.environ.update({k: v for k, v in env.items() if v is not None})
    os.environ["MOXIE_VOICE_BASE_URL"] = BASE
    os.environ.setdefault("MOXIE_VOICE_API_KEY", KEY)
    import config as _c
    module = importlib.reload(_c)
    for k, v in keep.items():                       # leave the process as we found it
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v
    return module


def _speak(synth, text=MOXIE_LINE):
    """One gateway call. Returns (pcm, rate, channels, seconds-on-the-wire)."""
    with A.Stage("tts") as s:
        pcm = synth.synthesize(text)
    return pcm, synth.sample_rate, synth.channels, s.seconds


def test_gateway_voice_is_real_speech_a_whisper_can_read():
    """The proof. `MOXIE_VOICE_BASE_URL` + `MOXIE_VOICE_MODEL` is the whole switch; the
    bytes that come back are unwrapped from the WAV the gateway sends (labelled
    `audio/mpeg`, which we ignore), carry the header's own rate, and transcribe back into
    the sentence we asked it to say."""
    from moxie_sdk.tts import FallbackSynthesizer
    c = _config(MOXIE_VOICE_MODEL=MODEL, MOXIE_VOICE_FORMAT="wav")
    synth = c.build_synthesizer()
    assert isinstance(synth, FallbackSynthesizer), synth
    assert synth.voice_name == "openai-voice"

    pcm, rate, channels, wire_s = _speak(synth)
    assert not synth.failed, "the gateway call fell through to the standby voice"
    _CACHE["amy"] = (pcm, rate)

    audio16 = A.resample_pcm16(pcm, rate, A.ROBOT_SAMPLE_RATE)
    with A.Stage("stt") as t_stt:
        heard = _whisper().transcribe(audio16, A.ROBOT_SAMPLE_RATE)
    ratio = A.word_overlap(MOXIE_LINE, heard)
    print(f"\n[gw] model={MODEL} format=wav said={MOXIE_LINE!r}"
          f"\n[gw] heard={heard!r}"
          f"\n[gw] overlap={ratio:.2f} rate={rate} Hz ch={channels} bytes={len(pcm)} "
          f"audio={A.duration_s(pcm, rate):.2f}s latency={wire_s:.2f}s "
          f"{A.timing_line(t_stt)}")

    assert channels == 1 and rate > 0
    assert len(pcm) % 2 == 0, "not 16-bit PCM"
    assert heard.strip(), "the gateway produced audio Whisper could not read at all"
    assert ratio >= STT_FLOOR, (
        f"gateway → Whisper recovered only {ratio:.2f} of the sentence\n"
        f"  said : {MOXIE_LINE!r}\n  heard: {heard!r}")


def test_the_gateway_round_trip_cannot_pass_on_the_placeholder_tone():
    """The guard that makes the test above mean something: run the SAME pipeline on
    `ToneSynthesizer` output and show it is not speech — by spectrum, by zero-crossing
    behavior, and by ASR. Costs no gateway call (it reuses the audio from the first
    test and synthesizes the tone locally)."""
    from moxie_sdk.tts import ToneSynthesizer
    if "amy" not in _CACHE:
        pytest.skip("the gateway tier-1 test did not run")
    pcm, rate = _CACHE["amy"]
    speech16 = A.resample_pcm16(pcm, rate, A.ROBOT_SAMPLE_RATE)
    tone16 = A.resample_pcm16(ToneSynthesizer().synthesize(MOXIE_LINE), 22050,
                              A.ROBOT_SAMPLE_RATE)

    flat_tone, flat_speech = A.spectral_flatness(tone16), A.spectral_flatness(speech16)
    zcr_tone, zcr_speech = A.zcr_std(tone16), A.zcr_std(speech16)
    heard_tone = _whisper().transcribe(tone16, A.ROBOT_SAMPLE_RATE)
    print(f"\n[gw] tone    flatness={flat_tone:.3e} zcr_std={zcr_tone:.4f} "
          f"whisper={heard_tone!r}"
          f"\n[gw] gateway flatness={flat_speech:.3e} zcr_std={zcr_speech:.4f}")

    assert flat_speech > 100 * flat_tone, (flat_speech, flat_tone)
    assert flat_speech > 1e-3, f"suspiciously tonal 'speech': flatness={flat_speech:.3e}"
    assert zcr_speech > 10 * zcr_tone, (zcr_speech, zcr_tone)
    assert A.word_overlap(MOXIE_LINE, heard_tone) < 0.2, (
        f"the tone transcribed as words — the guard is broken: {heard_tone!r}")


def test_the_model_name_is_the_voice_switch():
    """`piper-amy` → `piper-ryan` is a one-variable change and a genuinely different
    voice: different bytes for the same sentence, still intelligible. (The `voice`
    request field is derived from the model name and IGNORED by the gateway — the model
    is what picks the voice.)"""
    if ALT_MODEL == MODEL:
        pytest.skip("MOXIE_VOICE_ALT_MODEL matches the primary model")
    c = _config(MOXIE_VOICE_MODEL=ALT_MODEL, MOXIE_VOICE_FORMAT="wav")
    synth = c.build_synthesizer()
    pcm, rate, _, wire_s = _speak(synth)
    assert not synth.failed, f"{ALT_MODEL} fell through to the standby voice"

    heard = _whisper().transcribe(A.resample_pcm16(pcm, rate, A.ROBOT_SAMPLE_RATE),
                                  A.ROBOT_SAMPLE_RATE)
    ratio = A.word_overlap(MOXIE_LINE, heard)
    other = _CACHE.get("amy")
    print(f"\n[gw] model={ALT_MODEL} heard={heard!r}"
          f"\n[gw] overlap={ratio:.2f} rate={rate} Hz bytes={len(pcm)} "
          f"audio={A.duration_s(pcm, rate):.2f}s latency={wire_s:.2f}s"
          + (f" (vs {MODEL}: {len(other[0])} B @ {other[1]} Hz)" if other else ""))
    assert ratio >= ALT_FLOOR, (f"{ALT_MODEL} recovered only {ratio:.2f}\n"
                                f"  said : {MOXIE_LINE!r}\n  heard: {heard!r}")
    if other:
        assert pcm != other[0], "the two models returned byte-identical audio"


def test_pcm_format_returns_raw_frames_at_the_configured_rate():
    """`response_format="pcm"` is the same speech with no container: nothing in the
    payload can state its rate, so the configured one is what the CloudTTSResponse
    carries — and it must agree with what the WAV header said."""
    c = _config(MOXIE_VOICE_MODEL=MODEL, MOXIE_VOICE_FORMAT="pcm",
                MOXIE_VOICE_SAMPLE_RATE="22050")
    synth = c.build_synthesizer()
    pcm, rate, _, wire_s = _speak(synth)
    assert not synth.failed and rate == 22050
    assert pcm[:4] != b"RIFF", "pcm came back wrapped in a RIFF container"

    wav = _CACHE.get("amy")
    print(f"\n[gw] format=pcm rate={rate} Hz bytes={len(pcm)} "
          f"audio={A.duration_s(pcm, rate):.2f}s latency={wire_s:.2f}s"
          + (f" (wav was {len(wav[0])} B @ {wav[1]} Hz)" if wav else ""))
    if wav:
        assert wav[1] == rate, (
            f"the WAV header said {wav[1]} Hz but pcm is configured at {rate} Hz")
        # same sentence, same voice, same rate → the same number of frames, ±2%
        assert abs(len(pcm) - len(wav[0])) <= 0.02 * len(wav[0]), (len(pcm), len(wav[0]))


def test_an_unusable_model_downgrades_instead_of_going_silent():
    """A child never hears silence because the gateway hiccupped. An unknown model is
    the one failure we can provoke on demand; the synthesizer surfaces it once and the
    standby finishes the sentence."""
    c = _config(MOXIE_VOICE_MODEL="piper-does-not-exist", MOXIE_VOICE_FORMAT="wav")
    synth = c.build_synthesizer()
    logged = []
    synth._log = logged.append
    with A.Stage("tts") as s:
        audio = synth.synthesize(MOXIE_LINE)
    print(f"\n[gw] unknown-model fallback in {s.seconds:.2f}s -> "
          f"{synth.voice_name} {len(audio)} B @ {synth.sample_rate} Hz"
          f"\n[gw] logged: {logged}")
    assert synth.failed and audio, "an unusable gateway model left the turn silent"
    assert synth.voice_name == synth._standby.name
    assert len(logged) == 1 and "openai-voice" in logged[0]
