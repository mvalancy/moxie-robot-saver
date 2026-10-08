"""
Live ONE-TURN end-to-end on the assembled appliance: the gateway BRAIN and the gateway
VOICE at the same time, through the shipped entry point.

The neighbours prove halves (`test_live_gateway.py` the brain, `test_live_gateway_tts.py`
the voice in-process, `sim/run_smoke.sh` a real broker with echo + tone). This asserts
what an owner runs: `mqtt/run.py`, `MOXIE_APP=llm` + `MOXIE_VOICE_BASE_URL`, a robot on
MQTT hearing a real sentence back.

So this file boots the REAL stack (`helpers_stack.Stack`: mosquitto on a free port,
`mqtt/run.py` in a subprocess with its own scratch `MOXIE_DATA_DIR`) and lets the
protocol-faithful SIL robot (`sim/virtual_moxie.py`, in-process so the audio is readable)
take exactly ONE turn:

    state → config(paired) → events/remote-chat "hello Moxie"
                           → commands/remote_chat  (the gateway's own words)
                           → commands/tts          (the gateway's own voice)

**Budget: 1 chat completion + 1 `/audio/speech`** — one module-scoped turn that every test
reads. `ToneSynthesizer` emits the same 22050 Hz PCM, so speech is told from the
placeholder by spectral flatness (`helpers_audio`; proven unable to pass on the tone,
creds-free, in `test_speech_guard.py`).

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
from moxie_sdk.types import ResultCode                        # noqa: E402

load_repo_dotenv()          # mqtt/.env of this tree or the main checkout

VOICE_BASE = (os.environ.get("MOXIE_VOICE_BASE_URL") or "").strip()
KEY = (os.environ.get("MOXIE_VOICE_API_KEY")
       or os.environ.get("MOXIE_LLM_API_KEY")
       or os.environ.get("LITELLM_MASTER_KEY") or "")
CHAT_BASE = (os.environ.get("MOXIE_LLM_BASE_URL") or "").strip()
MODEL = (os.environ.get("MOXIE_VOICE_MODEL") or "piper-amy").strip()

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
    assert reply["command"] == "remote_chat" and reply["result"] == ResultCode.SUCCESS, reply
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
