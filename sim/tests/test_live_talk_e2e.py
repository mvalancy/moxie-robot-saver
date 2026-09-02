"""
Live TALK end-to-end — real speech in, real speech out.

Everything else in the suite proves the *shape* of the voice path: `test_tts.py`
synthesizes with an injected `voice_fn`, `test_stt.py` accumulates VAD frames around a
fake transcriber, and `sim/run_smoke.sh` round-trips a `CloudTTSResponse` whose audio is
the built-in `ToneSynthesizer` beep. None of that is speech. A tone survives every one
of those tests, which is exactly the gap DoD criterion 6 called out: "live voice (real
STT/TTS speech, not the tone synth)" was the last thing not live-proven.

This file closes it in two tiers.

**Tier 1 — the voice is intelligible.** `PiperSynthesizer` (Amy, Moxie's voice) speaks a
fixed sentence; the PCM is resampled to the robot's 16 kHz and handed to the real
`WhisperTranscriber`. The transcript must recover the sentence (word overlap ≥ 0.7), and
a companion test proves the same pipeline *rejects* the placeholder: the tone's spectral
flatness is ~10 orders of magnitude below Amy's and Whisper hears no words in it. So this
test cannot pass on tone output — which is the whole point of writing it.

**Tier 2 — the talk loop.** A second, deliberately *different* Piper voice (Lessac) plays
the child. Its audio is chopped into `zmqSTTRequest` protobuf frames — START_OF_SPEECH,
SPEECH…, END_OF_SPEECH, hand-encoded by `helpers_audio.pb_zmq_stt_frame` — and pushed at
the REAL `MoxieRuntime` through `_on_event(device_id, "zmq", …)`, the exact entry point
`_on_message` uses for a robot's `events/zmq` traffic. The runtime transcribes, publishes
a `zmqSTTResponse`, the transcript drives a turn, and the runtime answers with a
spec-conformant `RemoteChatResponse` **and** a `CloudTTSResponse` whose audio is
transcribed back and matched against the reply text. Two variants: the shipped
`starter.json` `globals[]` handler (0 gateway calls) and the same module on the real
gateway (1 call).

Thresholds are ratios, not equality: ASR is lossy and a temperature-0.8 model is not
reproducible (same reasoning as `test_live_action_tags.py`). The observed values are far
above the floors — see the printed `[talk]` lines, which also carry the per-stage wall
clock the "~20 s reprompt window" audit asked for.

Skips cleanly and instantly without piper / faster-whisper / the voice files / a gateway
key, so the hermetic CI run is unaffected.

    .venv/bin/python -m pytest sim/tests/test_live_talk_e2e.py -q -s
"""
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.dirname(__file__))

# Hard requirements for this whole file. Both are absent in CI's hermetic env, so
# collection skips here and costs nothing.
pytest.importorskip("piper", reason="piper-tts not installed (live voice test)")
pytest.importorskip("faster_whisper", reason="faster-whisper not installed (live STT test)")
pytest.importorskip("numpy")

import helpers_audio as A                                    # noqa: E402
from helpers_runtime import (FakeClient, assert_spec_response, drive_turn,  # noqa: E402,F401
                             load_repo_dotenv, make_runtime)

STARTER = os.path.join(REPO, "mqtt", "content_modules", "starter.json")

# What Moxie says in tier 1. Fixed, ordinary, child-facing English — no proper nouns
# Whisper has never heard, because the assertion is about our audio, not about ASR
# vocabulary.
MOXIE_LINE = "Hi Sam, I am Moxie. Do you want to hear a story about a brave little robot?"

# Tier-2 child utterances. The first hits the shipped module's `globals[]` Timer regex
# (`timer for (\d+) (minute|second)`) — Whisper writes "5 minutes", so the regex matches
# a real transcript, not a hand-written one.
CHILD_TIMER = "Hey Moxie, please set a timer for 5 minutes."
CHILD_JOKE = "Hi Moxie, tell me a joke!"

#: What ContentApp says when the gateway never answers (content_app.py) — a live
#: turn that lands on this line proves the plumbing, not the brain.
_DEGRADED = "Give me one tiny second to think"

# Acceptance floors (see the module docstring for why these are ratios).
STT_FLOOR = 0.7          # tier 1: Piper speech recovered by Whisper
TURN_FLOOR = 0.6         # tier 2: the child's utterance as the runtime heard it
REPLY_FLOOR = 0.5        # tier 2: Moxie's reply after a full TTS → STT round trip

pytestmark = pytest.mark.skipif(
    not (A.MOXIE_VOICE and A.CHILD_VOICE),
    reason="Piper voices not installed (sim/tts/voices/*.onnx are git-ignored; "
           "set MOXIE_VOICES_DIR to point at them)")


load_repo_dotenv()          # mqtt/.env from this tree or the main checkout
KEY = os.environ.get("MOXIE_LLM_API_KEY") or os.environ.get("LITELLM_MASTER_KEY") or ""
BASE = os.environ.get("MOXIE_LLM_BASE_URL", "https://gateway.graphlings.net/v1")
MODEL = os.environ.get("MOXIE_LLM_MODEL", "graphling-medium")

# Loading a 63 MB voice or the Whisper weights takes ~1 s each; share them across tests.
_CACHE = {}


def _piper(path):
    from moxie_sdk.tts import PiperSynthesizer
    if path not in _CACHE:
        with A.Stage("load") as s:
            _CACHE[path] = PiperSynthesizer(path)
        print(f"\n[talk] loaded piper {os.path.basename(path)} "
              f"({_CACHE[path].sample_rate} Hz) in {s.seconds:.2f}s")
    return _CACHE[path]


def _whisper():
    from moxie_sdk.stt import WhisperTranscriber
    if "whisper" not in _CACHE:
        with A.Stage("load") as s:
            _CACHE["whisper"] = WhisperTranscriber("base.en", device="cpu",
                                                   compute_type="int8")
        print(f"\n[talk] loaded faster-whisper base.en (int8/cpu) in {s.seconds:.2f}s")
    return _CACHE["whisper"]


def _say(synth, text):
    """Synthesize `text` and hand back 16 kHz PCM16 — what the robot's mic would carry —
    plus the synthesis stage timer."""
    with A.Stage("tts") as s:
        pcm = synth.synthesize(text)
    return A.resample_pcm16(pcm, synth.sample_rate, A.ROBOT_SAMPLE_RATE), s


# ============================================================ tier 1: voice ==
def test_piper_speech_survives_a_whisper_round_trip():
    """Amy speaks; Whisper reads it back. Proof that our TTS emits SPEECH, not audio."""
    synth, whisper = _piper(A.MOXIE_VOICE), _whisper()
    pcm16, t_tts = _say(synth, MOXIE_LINE)
    with A.Stage("stt") as t_stt:
        heard = whisper.transcribe(pcm16, A.ROBOT_SAMPLE_RATE)
    ratio = A.word_overlap(MOXIE_LINE, heard)
    print(f"\n[talk] tier1 said={MOXIE_LINE!r}\n"
          f"[talk] tier1 heard={heard!r}\n"
          f"[talk] tier1 overlap={ratio:.2f} audio={A.duration_s(pcm16, 16000):.2f}s "
          f"{A.timing_line(t_tts, t_stt)}")
    assert heard.strip(), "Piper produced audio Whisper could not read at all"
    assert ratio >= STT_FLOOR, (
        f"Piper→Whisper recovered only {ratio:.2f} of the sentence\n"
        f"  said : {MOXIE_LINE!r}\n  heard: {heard!r}")


def test_the_round_trip_cannot_pass_on_the_placeholder_tone():
    """The guard that makes tier 1 mean something: run the SAME pipeline on
    `ToneSynthesizer` output and show it is not speech — by spectrum and by ASR."""
    from moxie_sdk.tts import ToneSynthesizer
    tone_synth = ToneSynthesizer()
    tone16, _ = _say(tone_synth, MOXIE_LINE)
    speech16, _ = _say(_piper(A.MOXIE_VOICE), MOXIE_LINE)

    flat_tone, flat_speech = A.spectral_flatness(tone16), A.spectral_flatness(speech16)
    zcr_tone, zcr_speech = A.zcr_std(tone16), A.zcr_std(speech16)
    heard_tone = _whisper().transcribe(tone16, A.ROBOT_SAMPLE_RATE)
    print(f"\n[talk] tone   flatness={flat_tone:.3e} zcr_std={zcr_tone:.4f} "
          f"whisper={heard_tone!r}\n"
          f"[talk] piper  flatness={flat_speech:.3e} zcr_std={zcr_speech:.4f}")

    # A sine has all its energy in one bin (flatness → 0); speech has formants + noise.
    assert flat_speech > 100 * flat_tone, (flat_speech, flat_tone)
    assert flat_speech > 1e-3, f"suspiciously tonal 'speech': flatness={flat_speech:.3e}"
    # Voiced/unvoiced alternation: the tone's zero-crossing rate barely moves.
    assert zcr_speech > 10 * zcr_tone, (zcr_speech, zcr_tone)
    # And the decisive one: the tone carries no words.
    assert A.word_overlap(MOXIE_LINE, heard_tone) < 0.2, (
        f"the tone transcribed as words — the guard is broken: {heard_tone!r}")


# ======================================================== tier 2: talk loop ==
def _hear_utterance(rt, device_id, pcm16, uuid="utt-live-1"):
    """Stream one utterance at the runtime as a real robot does: `events/zmq` frames
    carrying a `zmqSTTRequest` protobuf. Returns (transcript, stage_timer)."""
    frames = A.stt_frames(pcm16, uuid)
    transcript = None
    with A.Stage("stt") as s:
        for frame in frames:
            out = rt._on_event(device_id, "zmq", frame)
            if out is not None:
                transcript = out
    # the runtime must also answer the robot with a FINAL zmqSTTResponse
    published = rt.client.on(f"/devices/{device_id}/commands/zmq")
    assert published, f"no zmqSTTResponse published; saw {rt.client.published!r}"
    final = published[-1]
    assert final["type"] == "FINAL" and final["uuid"] == uuid, final
    assert final["speech"] == transcript, (final, transcript)
    return transcript, s


def _speak_back(resp_tts):
    """A published CloudTTSResponse → (transcript of its audio, seconds, stage timer)."""
    from moxie_sdk.tts import decode_cloud_tts_response
    decoded = decode_cloud_tts_response(resp_tts)
    audio16 = A.resample_pcm16(decoded["audio"], decoded["sample_rate"],
                               A.ROBOT_SAMPLE_RATE)
    with A.Stage("tts-stt") as s:
        heard = _whisper().transcribe(audio16, A.ROBOT_SAMPLE_RATE)
    return heard, A.duration_s(audio16, A.ROBOT_SAMPLE_RATE), s, decoded


def _talk(app, utterance, *, device_id, event_id, label):
    """The whole loop once: child speaks → runtime hears → brain answers → Moxie speaks.
    Returns the RemoteChatResponse so the caller can make its own assertions."""
    conv_module_id, conv_content_id = "FREE_CHAT", "default"
    rt, device_id = make_runtime(app, device_id=device_id,
                                 module_id=conv_module_id, content_id=conv_content_id)
    rt.set_transcriber(_whisper())
    rt.set_synthesizer(_piper(A.MOXIE_VOICE))

    child_pcm, t_child = _say(_piper(A.CHILD_VOICE), utterance)
    heard, t_stt = _hear_utterance(rt, device_id, child_pcm)
    turn_ratio = A.word_overlap(utterance, heard)

    with A.Stage("brain") as t_brain:
        resp = drive_turn(rt, device_id, heard, event_id=event_id)
    assert_spec_response(resp, event_id=event_id)
    reply_text = resp["output"]["text"]

    tts_msgs = rt.client.on(f"/devices/{device_id}/commands/tts")
    assert tts_msgs, f"runtime published no CloudTTSResponse; saw {rt.client.published!r}"
    spoken, spoken_s, t_back, decoded = _speak_back(tts_msgs[-1])
    reply_ratio = A.word_overlap(reply_text, spoken)

    print(f"\n[talk] {label} child said : {utterance!r}"
          f"\n[talk] {label} runtime heard: {heard!r}  (overlap {turn_ratio:.2f})"
          f"\n[talk] {label} moxie replied: {reply_text!r}"
          f"\n[talk] {label} moxie spoken back: {spoken!r}  (overlap {reply_ratio:.2f},"
          f" {spoken_s:.2f}s of {decoded['sample_rate']} Hz audio,"
          f" {len(decoded['audio'])} B)"
          f"\n[talk] {label} timings: child-{t_child}  {t_stt}  {t_brain}  {t_back}"
          f"  loop={t_stt.seconds + t_brain.seconds:.2f}s"
          f"  (child audio {A.duration_s(child_pcm, 16000):.2f}s)")

    assert decoded["audio"], "CloudTTSResponse carried no audio"
    assert decoded["sample_rate"] == _piper(A.MOXIE_VOICE).sample_rate
    assert turn_ratio >= TURN_FLOOR, (
        f"the runtime misheard the child ({turn_ratio:.2f})\n"
        f"  said : {utterance!r}\n  heard: {heard!r}")
    assert reply_ratio >= REPLY_FLOOR, (
        f"Moxie's spoken audio does not match her own reply ({reply_ratio:.2f})\n"
        f"  reply: {reply_text!r}\n  spoken back: {spoken!r}")
    return resp


def _shipped_module():
    from moxie_sdk.content import load_modules
    with open(STARTER) as fh:
        return load_modules(json.load(fh))


def test_full_talk_loop_with_a_global_handler_and_no_llm_call():
    """The complete voice loop on the shipped `starter.json`, answered by its
    `globals[]` Timer handler — real speech in, real speech out, zero gateway calls.
    This is the cheap, deterministic proof; the live-brain test below is the same loop
    with the real model behind it."""
    pytest.importorskip("jinja2")
    pytest.importorskip("paho.mqtt.client")
    from moxie_sdk.content import ContentApp

    calls = []

    def _no_brain(messages):                     # must never be reached
        calls.append(messages)
        raise AssertionError("a matched global spent an LLM call")

    def timer_handler(volley, session):
        amount, unit = (volley.entities + ["5", "minute"])[:2]
        volley.set_output(f"Okay! A timer for {amount} {unit}s. Go race a robot!")

    app = ContentApp(_shipped_module(), _no_brain,
                     global_handlers={"Timer": timer_handler})
    resp = _talk(app, CHILD_TIMER, device_id="d_talk_global",
                 event_id="evt-talk-global", label="global")
    assert not calls, "the global did not short-circuit the brain"
    assert "timer" in resp["output"]["text"].lower(), resp
    # The regex matched a REAL transcript, not a hand-typed string — that is the point.
    assert "5" in resp["output"]["text"], resp


@pytest.mark.skipif(not KEY, reason="no gateway key (set MOXIE_LLM_API_KEY in mqtt/.env)")
def test_full_talk_loop_through_the_live_brain():
    """The same loop with the real graphling brain in the middle: one gateway call, and
    every leg of it (mic audio → transcript → model → speaker audio) is real."""
    pytest.importorskip("openai")
    pytest.importorskip("jinja2")
    pytest.importorskip("paho.mqtt.client")
    from moxie_sdk.chat import make_openai_chat
    from moxie_sdk.content import ContentApp

    app = ContentApp(_shipped_module(),
                     make_openai_chat(BASE, KEY, MODEL, max_tokens=96))
    resp = _talk(app, CHILD_JOKE, device_id="d_talk_live",
                 event_id="evt-talk-live", label="live")
    text = resp["output"]["text"]
    assert len(text) > 10, f"suspiciously short live reply: {resp}"
    assert "<" not in text, "markup leaked into the spoken text"
    # A gateway that 429s past the SDK's backoff makes ContentApp answer with its canned
    # degradation line. The voice loop above still proved itself on that line, but the
    # words did not come from the model — so say so rather than bank a green that only
    # means "the fallback works" (same honesty rule as the tag-rate tests).
    if text.startswith(_DEGRADED):
        pytest.skip("gateway degraded to the canned fallback (429s past backoff); the "
                    "voice loop passed but no real completion reached it")
