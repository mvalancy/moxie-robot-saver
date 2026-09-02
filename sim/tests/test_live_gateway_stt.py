"""
Live GATEWAY EARS — real audio into our LiteLLM proxy's `/v1/audio/transcriptions`.

`test_stt_gateway.py` proves the *shape* of the cloud transcriber against a fake client
(the WAV wrapping, the request fields, the backoff, the latch, the `MOXIE_STT`
precedence). Every one of those tests would still pass if the gateway transcribed
everything as "banana". This file is the one that cannot: **the gateway must read back
the sentence we made it say**, at word overlap ≥ 0.7, twice — once at the audio's own
22050 Hz and once at the 16 kHz the robot's perception bus actually carries.

Four tests, budgeted at **eight gateway calls total**:

1. `piper-amy` speaks a fixed 13-word line (1 TTS) → `stt-whisper` reads it back at the
   WAV's own rate (1 STT).
2. the same audio, resampled to 16 kHz with the standard library alone (no numpy — a
   hosted box that installed only `openai` must be able to run this), read back again
   (1 STT). This is the rate the runtime hands over, so it is the one that matters.
3. an unknown STT model (1 STT, a 400) → `FallbackTranscriber` latches to the standby
   inside one call, reports once, and `describe()` says who is listening now.
4. **the whole loop through the real `MoxieRuntime`**: a child utterance spoken by
   `piper-ryan` (1 TTS) → `zmqSTTRequest` frames → gateway STT (1 STT) → the gateway
   brain (1 chat) → a spec `RemoteChatResponse` → gateway TTS (1 TTS) → a
   `CloudTTSResponse` the SIM could play. Three backends, one turn, no local models.

Everything is built by `config.build_transcriber()` / `build_synthesizer()` /
`build_app()`, so what is under test is the shipped switch (`MOXIE_STT=gateway`), not a
test-local client. Runs when a gateway base URL and key are present (`mqtt/.env` of this
tree or of the main checkout) and skips cleanly and instantly otherwise, so the hermetic
tier is unaffected.

    MOXIE_STT=gateway .venv/bin/python -m pytest sim/tests/test_live_gateway_stt.py -q -s
"""
import importlib
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MQTT = os.path.join(REPO, "mqtt")
sys.path.insert(0, MQTT)
sys.path.insert(0, os.path.join(MQTT, "supervisor"))
sys.path.insert(0, os.path.dirname(__file__))

pytest.importorskip("openai", reason="openai SDK not installed (live gateway test)")

import helpers_audio as A                                    # noqa: E402
from helpers_runtime import load_repo_dotenv                 # noqa: E402

load_repo_dotenv()          # mqtt/.env from this tree or the main checkout

BASE = (os.environ.get("MOXIE_STT_BASE_URL")
        or os.environ.get("MOXIE_VOICE_BASE_URL")
        or "").strip()
KEY = (os.environ.get("MOXIE_STT_API_KEY")
       or os.environ.get("MOXIE_VOICE_API_KEY")
       or os.environ.get("MOXIE_LLM_API_KEY")
       or os.environ.get("LITELLM_MASTER_KEY") or "")
STT_MODEL = (os.environ.get("MOXIE_STT_MODEL") or "stt-whisper").strip()
#: The voice endpoint. Same host as the ears on our gateway; kept separate so a split
#: deployment (ears here, voice there) still runs this file.
VOICE_BASE = (os.environ.get("MOXIE_VOICE_BASE_URL") or BASE).strip()
TTS_MODEL = (os.environ.get("MOXIE_VOICE_MODEL") or "piper-amy").strip()
#: A second voice, so the child in test 4 does not sound like Moxie herself.
CHILD_MODEL = os.environ.get("MOXIE_VOICE_ALT_MODEL", "piper-ryan").strip()

pytestmark = pytest.mark.skipif(
    not (BASE and KEY),
    reason="no gateway STT configured (set MOXIE_VOICE_BASE_URL / MOXIE_STT_BASE_URL "
           "+ a key in mqtt/.env)")

#: 13 words of ordinary, child-facing English — no proper nouns an ASR has never heard,
#: because the assertion is about the round trip, not about vocabulary. The same line the
#: TTS tier speaks, so the two runs are directly comparable.
MOXIE_LINE = "Hi Sam, I am Moxie. Do you want to hear a story about a brave little robot?"
#: What the child says in test 4.
CHILD_LINE = "Hi Moxie, can you tell me a joke about a robot?"

STT_FLOOR = 0.7          # the gateway's own speech, read back by the gateway's own ears
TURN_FLOOR = 0.6         # the runtime's transcript of the child (a second voice)

_CACHE = {}


def _config(**env):
    """Import `mqtt/config.py` with a controlled environment — the shipped switch, not a
    hand-rolled client. Returns the reloaded module and leaves the process as it was."""
    keys = ("MOXIE_STT", "MOXIE_STT_MODEL", "MOXIE_STT_BASE_URL", "MOXIE_STT_API_KEY",
            "MOXIE_VOICE_BASE_URL", "MOXIE_VOICE_MODEL", "MOXIE_VOICE_FORMAT",
            "MOXIE_VOICE_SAMPLE_RATE", "MOXIE_TTS", "MOXIE_PIPER_MODEL", "MOXIE_APP",
            "MOXIE_STREAMING", "MOXIE_BRAIN_BUDGET_S")
    keep = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    os.environ.update({k: v for k, v in env.items() if v is not None})
    os.environ["MOXIE_STT_BASE_URL"] = BASE
    os.environ["MOXIE_VOICE_BASE_URL"] = VOICE_BASE
    os.environ.setdefault("MOXIE_STT_API_KEY", KEY)
    import config as _c
    module = importlib.reload(_c)
    for k, v in keep.items():
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v
    return module


def _gateway_ears(**env):
    """`config.build_transcriber()` with `MOXIE_STT=gateway` — a `FallbackTranscriber`
    whose primary is the cloud and whose standby is whatever this box could offer."""
    from moxie_sdk.stt import FallbackTranscriber
    trans = _config(MOXIE_STT="gateway", **env).build_transcriber()
    assert isinstance(trans, FallbackTranscriber), trans
    return trans


def _moxie_voice(model=None):
    """`config.build_synthesizer()` on the gateway voice — the shipped switch again."""
    from moxie_sdk.tts import FallbackSynthesizer
    synth = _config(MOXIE_VOICE_MODEL=model or TTS_MODEL,
                    MOXIE_VOICE_FORMAT="wav").build_synthesizer()
    assert isinstance(synth, FallbackSynthesizer), synth
    return synth


def _speak(synth, text):
    with A.Stage("tts") as s:
        pcm = synth.synthesize(text)
    assert not synth.failed, "the gateway voice fell through to the standby"
    return pcm, synth.sample_rate, s


def _hear(trans, pcm, rate, *, label, said):
    with A.Stage("stt") as s:
        heard = trans.transcribe(pcm, rate)
    ratio = A.word_overlap(said, heard)
    print(f"\n[gw-stt] {label} model={trans.engine.model if hasattr(trans, 'engine') else STT_MODEL}"
          f" rate={rate} Hz bytes={len(pcm)} audio={A.duration_s(pcm, rate):.2f}s"
          f"\n[gw-stt] {label} heard={heard!r}"
          f"\n[gw-stt] {label} overlap={ratio:.2f} latency={s.seconds:.2f}s")
    return heard, ratio, s


# ================================================== 1. the gateway's own rate ==
def test_the_gateway_reads_back_its_own_voice():
    """The proof. `MOXIE_STT=gateway` is the whole switch: the gateway speaks a sentence,
    the gateway transcribes it, and the words survive the round trip."""
    synth = _moxie_voice()
    pcm, rate, t_tts = _speak(synth, MOXIE_LINE)
    _CACHE["audio"] = (pcm, rate)
    print(f"\n[gw-stt] said={MOXIE_LINE!r} voice={TTS_MODEL} tts={t_tts.seconds:.2f}s")

    ears = _gateway_ears()
    print(f"[gw-stt] ears={ears.describe()}")
    heard, ratio, _ = _hear(ears, pcm, rate, label="native", said=MOXIE_LINE)

    assert not ears.failed, "the gateway ears fell through to the standby"
    assert heard.strip(), "the gateway returned an empty transcript for real speech"
    assert ratio >= STT_FLOOR, (f"gateway STT recovered only {ratio:.2f}\n"
                                f"  said : {MOXIE_LINE!r}\n  heard: {heard!r}")


# ============================================ 2. the rate the robot actually is ==
def test_the_same_audio_at_the_robots_sixteen_kilohertz():
    """The perception bus carries 16 kHz mono PCM16, which is the rate `SttSession` hands
    the transcriber — so that is the rate the WAV header has to carry. Resampled with the
    **standard library only**: a hosted box with no numpy is exactly the deployment the
    cloud ears exist for."""
    if "audio" not in _CACHE:
        pytest.skip("the first gateway-STT test did not run")
    pcm, rate = _CACHE["audio"]
    pcm16 = A.resample_pcm16_stdlib(pcm, rate, A.ROBOT_SAMPLE_RATE)
    _CACHE["audio16"] = pcm16
    assert abs(A.duration_s(pcm16, 16000) - A.duration_s(pcm, rate)) < 0.05

    ears = _gateway_ears()
    heard, ratio, _ = _hear(ears, pcm16, A.ROBOT_SAMPLE_RATE, label="robot-16k",
                            said=MOXIE_LINE)
    assert not ears.failed
    assert ratio >= STT_FLOOR, (f"gateway STT at 16 kHz recovered only {ratio:.2f}\n"
                                f"  said : {MOXIE_LINE!r}\n  heard: {heard!r}")


# ================================================== 3. the downgrade, provoked ==
def test_an_unusable_model_downgrades_instead_of_crashing_the_turn():
    """A child's sentence must not end in a traceback because the gateway hiccupped. An
    unknown model is the one failure we can provoke on demand; the ears say so once and
    the standby finishes the run."""
    if "audio16" not in _CACHE:
        pytest.skip("the earlier gateway-STT tests did not run")
    ears = _gateway_ears(MOXIE_STT_MODEL="stt-does-not-exist")
    logged = []
    ears._log = logged.append

    with A.Stage("stt") as s:
        heard = ears.transcribe(_CACHE["audio16"], A.ROBOT_SAMPLE_RATE)
    print(f"\n[gw-stt] unknown-model fallback in {s.seconds:.2f}s -> "
          f"{ears.engine_name} heard={heard!r}"
          f"\n[gw-stt] describe={ears.describe()}"
          f"\n[gw-stt] logged: {logged}")

    assert ears.failed, "an unusable model did not downgrade"
    assert len(logged) == 1 and "openai-stt failed" in logged[0]
    assert ears.engine_name == ears._standby.name
    assert "failed" in ears.describe() and ears._standby.name in ears.describe()


# ======================================= 4. one child utterance, three backends ==
def test_a_child_utterance_through_the_runtime_on_gateway_ears_brain_and_voice():
    """The slice's whole point, in one turn: nothing local in the loop. A child speaks
    (gateway TTS, a second voice), the REAL `MoxieRuntime` hears through `events/zmq`
    frames (gateway STT), answers (gateway LLM) and speaks back (gateway TTS)."""
    pytest.importorskip("jinja2")
    pytest.importorskip("paho.mqtt.client")
    from helpers_runtime import assert_spec_response, drive_turn, make_runtime
    from moxie_sdk.tts import decode_cloud_tts_response

    c = _config(MOXIE_STT="gateway", MOXIE_APP="llm", MOXIE_VOICE_MODEL=TTS_MODEL,
                MOXIE_VOICE_FORMAT="wav")
    trans, synth, app = c.build_transcriber(), c.build_synthesizer(), c.build_app()
    print(f"\n[gw-stt] [run] STT enabled: {trans.describe()}"
          f"\n[gw-stt] [run] server voice enabled: {synth.describe()}"
          f"\n[gw-stt] [run] brain: {app.name} · {c.LLM_MODEL} @ {c.LLM_BASE_URL}")

    rt, device_id = make_runtime(app, device_id="d_gw_stt", nickname="Sam")
    rt.set_transcriber(trans)
    rt.set_synthesizer(synth)
    # One reply, one voice call: a filler chunk and a streamed sentence would each get
    # their own CloudTTSResponse, and this test is budgeted at exactly one of each.
    rt.streaming, rt.brain_budget_s = False, 0.0

    child = _moxie_voice(CHILD_MODEL)
    child_pcm, child_rate, t_child = _speak(child, CHILD_LINE)
    pcm16 = A.resample_pcm16_stdlib(child_pcm, child_rate, A.ROBOT_SAMPLE_RATE)

    transcript = None
    with A.Stage("stt") as t_stt:
        for frame in A.stt_frames(pcm16, "utt-gw-stt"):
            out = rt._on_event(device_id, "zmq", frame)
            if out is not None:
                transcript = out
    assert transcript, "the runtime never produced a transcript for the child's audio"
    turn_ratio = A.word_overlap(CHILD_LINE, transcript)
    published = rt.client.on(f"/devices/{device_id}/commands/zmq")
    assert published and published[-1]["type"] == "FINAL", published

    with A.Stage("brain") as t_brain:
        resp = drive_turn(rt, device_id, transcript, event_id="evt-gw-stt")
    assert_spec_response(resp, event_id="evt-gw-stt")
    reply = resp["output"]["text"]

    tts_msgs = rt.client.on(f"/devices/{device_id}/commands/tts")
    assert tts_msgs, f"the runtime published no CloudTTSResponse; {rt.client.published!r}"
    spoken = decode_cloud_tts_response(tts_msgs[-1])

    print(f"\n[gw-stt] e2e child said : {CHILD_LINE!r}  (voice {CHILD_MODEL},"
          f" {A.duration_s(pcm16, 16000):.2f}s @ 16000 Hz, tts={t_child.seconds:.2f}s)"
          f"\n[gw-stt] e2e runtime heard: {transcript!r}  (overlap {turn_ratio:.2f},"
          f" {t_stt.seconds:.2f}s)"
          f"\n[gw-stt] e2e moxie replied: {reply!r}  ({t_brain.seconds:.2f}s)"
          f"\n[gw-stt] e2e 🔊 spoke {len(spoken['audio'])} B @ {spoken['sample_rate']} Hz"
          f" (~{A.duration_s(spoken['audio'], spoken['sample_rate']):.2f}s,"
          f" {len(spoken['marks'])} marks)"
          f"\n[gw-stt] e2e backends: ears={trans.engine_name} brain={c.LLM_MODEL}"
          f" voice={synth.voice_name}")

    assert not trans.failed, "the gateway ears fell through to the standby mid-turn"
    assert not synth.failed, "the gateway voice fell through to the standby mid-turn"
    assert trans.engine_name == "openai-stt" and synth.voice_name == "openai-voice"
    assert turn_ratio >= TURN_FLOOR, (
        f"the runtime misheard the child ({turn_ratio:.2f})\n"
        f"  said : {CHILD_LINE!r}\n  heard: {transcript!r}")
    assert len(reply) > 10, f"suspiciously short live reply: {resp}"
    assert spoken["audio"] and spoken["sample_rate"] > 0
    assert len(tts_msgs) == 1, f"the turn spent {len(tts_msgs)} voice calls, budgeted 1"
