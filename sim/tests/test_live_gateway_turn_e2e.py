"""
Live ONE-TURN end-to-end on the assembled appliance: the gateway BRAIN and the gateway
VOICE at the same time, through the shipped entry point.

Everything that already exists proves half of this. `test_live_gateway.py` proves the
brain answers. `test_live_gateway_tts.py` proves the voice speaks — but it builds the
synthesizer in-process and never puts a robot on a broker. `sim/run_smoke.sh` puts a real
robot on a real broker — with the echo app and the tone beep. Nothing asserted the thing
an owner actually runs: `mqtt/run.py`, one process, `MOXIE_APP=llm` +
`MOXIE_VOICE_BASE_URL`, a robot connecting over MQTT and hearing a real sentence back.

So this file boots the REAL stack (`helpers_stack.Stack`: mosquitto on a free port,
`mqtt/run.py` in a subprocess with its own scratch `MOXIE_DATA_DIR`) and lets the
protocol-faithful SIL robot (`sim/virtual_moxie.py`, in-process so the audio is readable)
take exactly ONE turn:

    state → config(paired) → events/remote-chat "hello Moxie"
                           → commands/remote_chat  (the gateway's own words)
                           → commands/tts          (the gateway's own voice)

**Budget: 1 chat completion + 1 `/audio/speech` request.** One stack boot, one turn, one
module-scoped fixture; every test below reads that single result. Nothing here re-asks.

The anti-tone guard is the point of the audio assertion: `ToneSynthesizer` also emits
22050 Hz mono PCM, so a sample rate proves nothing. Real speech is separated from the
placeholder by spectral flatness — six orders of magnitude apart — and that guard is
tested creds-free below, so a green here cannot mean "the fallback spoke".

Skips instantly without a gateway key, without a broker, or without numpy.

    .venv/bin/python -m pytest sim/tests/test_live_gateway_turn_e2e.py -q -s
"""
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "sim"))
sys.path.insert(0, os.path.dirname(__file__))

pytest.importorskip("paho.mqtt.client", reason="the SIL robot needs paho")
pytest.importorskip("numpy", reason="the speech/tone guard needs numpy")

import helpers_audio as A                                     # noqa: E402
import helpers_stack as S                                     # noqa: E402
from helpers_runtime import load_repo_dotenv                  # noqa: E402

load_repo_dotenv()          # mqtt/.env of this tree or the main checkout

VOICE_BASE = (os.environ.get("MOXIE_VOICE_BASE_URL") or "").strip()
KEY = (os.environ.get("MOXIE_VOICE_API_KEY")
       or os.environ.get("MOXIE_LLM_API_KEY")
       or os.environ.get("LITELLM_MASTER_KEY") or "")
CHAT_BASE = (os.environ.get("MOXIE_LLM_BASE_URL") or "").strip()
MODEL = (os.environ.get("MOXIE_VOICE_MODEL") or "piper-amy").strip()

#: Speech is broadband; the tone is one sine. Observed on this gateway: tone ~3.1e-12,
#: piper-amy ~5.2e-02 — thirteen orders of magnitude. A floor of 1e-6 is nowhere near
#: either, so it separates them without being tuned to today's numbers.
#:
#: Re-exported from `helpers_audio` rather than restated: this file used to carry its own
#: literal `1e-6`, and two copies of a threshold are two thresholds. The predicate every
#: assertion below actually calls is `helpers_audio.is_real_speech`, so the SIL suites,
#: the telehealth-voice suite and this one can never disagree about what "speech" means.
SPEECH_FLATNESS_FLOOR = A.SPEECH_FLATNESS_FLOOR


# --------------------------------------------------------------------------- #
# The guard itself — creds-free, so the live assertion below is never vacuous.
# --------------------------------------------------------------------------- #
def test_the_placeholder_tone_fails_the_speech_guard():
    """`ToneSynthesizer` speaks at 22050 Hz too. If the live test below could pass on
    tone output, it would be proving nothing — so prove it cannot, for free."""
    from moxie_sdk.tts import ToneSynthesizer
    tone = ToneSynthesizer()
    pcm = tone.synthesize("Hi Sam, I am Moxie.")
    assert tone.sample_rate == 22050, tone.sample_rate     # same rate as the gateway WAV
    flat = A.spectral_flatness(pcm)
    assert not A.is_real_speech(pcm), (
        f"the tone ({flat:.3e}) is above the speech floor — the guard is useless")
    assert SPEECH_FLATNESS_FLOOR == A.SPEECH_FLATNESS_FLOOR == 1e-6, \
        "the floor moved; re-check both directions of this guard before trusting it"


# --------------------------------------------------------------------------- #
# The live turn
# --------------------------------------------------------------------------- #
live = pytest.mark.skipif(
    not (VOICE_BASE and KEY),
    reason="no gateway configured (set MOXIE_VOICE_BASE_URL + a key in mqtt/.env)")


@pytest.fixture(scope="module")
def turn(tmp_path_factory):
    """ONE boot, ONE turn — 1 chat call + 1 TTS call for the whole module."""
    if not (VOICE_BASE and KEY):
        pytest.skip("no gateway configured")
    if not S.broker_available():
        pytest.skip("no mosquitto binary and no runnable docker — cannot boot a broker")
    from virtual_moxie import VirtualMoxie
    logs = str(tmp_path_factory.mktemp("live-stack"))
    env = {"MOXIE_APP": "llm",                    # the gateway BRAIN (config.build_app)
           "MOXIE_TTS": "",                       # …and let build_synthesizer's own
           "MOXIE_VOICE_BASE_URL": VOICE_BASE,    #    precedence pick the gateway VOICE
           "MOXIE_STREAMING": "off",              # one CloudTTSResponse, not a chunk queue
           # A filler line is itself a /audio/speech request. The budget for this file is
           # ONE, so the filler timer is put out of reach rather than raced with.
           "MOXIE_BRAIN_BUDGET_S": "300",
           "MOXIE_CHILD_NICKNAME": "Sam"}
    with S.Stack(logs, env=env) as stack:
        voice_line = stack.supervisor.line_with("server voice enabled")
        print(f"\n[live] {voice_line}")
        print(f"[live] {stack.supervisor.line_with('Moxie runtime')}")
        vm = VirtualMoxie("127.0.0.1", stack.port, timeout=120.0, verbose=True,
                          expect_tts=True)
        ok = vm.run_smoke()
        log = stack.supervisor.text()
    return dict(ok=ok, vm=vm, voice_line=voice_line, log=log, errors=list(vm.errors))


@live
def test_one_real_turn_round_trips_through_the_assembled_appliance(turn):
    assert turn["ok"], turn["errors"]
    reply = turn["vm"].reply_payload
    assert reply["command"] == "remote_chat" and reply["result"] == "SUCCESS", reply
    assert reply["backend"] == "router", reply
    text = (reply.get("output") or {}).get("text", "")
    assert text.strip(), reply
    assert (reply.get("output") or {}).get("markup", "").strip(), reply
    print(f"\n[live] brain said {text!r}")


@live
def test_the_supervisor_assembled_the_gateway_voice_not_a_local_one(turn):
    """`config.build_synthesizer()` precedence: a voice server outranks Piper and tone,
    and the loser becomes the standby. The startup line is the appliance saying so."""
    line = turn["voice_line"]
    assert "openai-voice" in line, line
    assert "standby:" in line, line          # FallbackSynthesizer, never bare
    assert "[voice] openai-voice failed" not in turn["log"], (
        "the gateway voice fell back mid-run — the audio below is the standby's")


@live
def test_the_audio_the_robot_heard_is_real_speech_not_the_tone(turn):
    spoke = turn["vm"].spoke
    assert spoke and spoke["audio"], turn["errors"]
    assert spoke["sample_rate"] == 22050, spoke["sample_rate"]   # the WAV header's own rate
    assert spoke["channels"] == 1, spoke["channels"]
    flat = A.spectral_flatness(spoke["audio"])
    seconds = A.duration_s(spoke["audio"], spoke["sample_rate"])
    print(f"\n[live] 🔊 {len(spoke['audio'])} B @ {spoke['sample_rate']} Hz "
          f"({seconds:.2f}s) flatness={flat:.3e} model={MODEL}")
    assert A.is_real_speech(spoke["audio"]), (
        f"flatness {flat:.3e} is tone-shaped — the gateway voice did not speak")
    assert seconds > 0.3, seconds


@live
def test_the_spoken_audio_is_the_reply_the_brain_actually_gave(turn):
    """One turn, one voice: the CloudTTSResponse must belong to the reply the robot got,
    not to a filler or a leftover chunk."""
    vm = turn["vm"]
    spoke, reply = vm.spoke, vm.reply_payload
    assert spoke["event_id"] in ("", None, reply.get("event_id")), (spoke, reply)
    words = len((reply.get("output") or {}).get("text", "").split())
    seconds = A.duration_s(spoke["audio"], spoke["sample_rate"])
    assert seconds >= 0.15 * words, (
        f"{seconds:.2f}s of audio for {words} words — that is not the whole reply")
