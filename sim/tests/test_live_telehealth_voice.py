"""
Live 🎭 TELEHEALTH VOICE — the operator's line, in Moxie's real mouth, on the real stack.

What already existed proves the *wire*: `sim/run_smoke.sh --telehealth` (and
`test_telehealth_runtime.py`) drive enable → start → speak → interrupt → end through a
real broker and assert every field of the recovered `TelehealthRobotCommand`. But the
supervisor there synthesizes with the zero-dep `ToneSynthesizer`, so what the robot
actually *played* was a beep. "A remote grown-up drives the body" is only true if the
body says the grown-up's words out loud in the voice a child would recognise.

So this file boots the SAME assembled appliance the owner runs — `helpers_stack.Stack`
(mosquitto on a free port + `mqtt/run.py` in a subprocess) — with the **gateway voice**
selected by `config.build_synthesizer()`'s own precedence, puts the protocol-faithful SIL
robot (`sim/virtual_moxie.py`, in-process so the audio is readable) on the broker, and
runs `run_telehealth()`. Then it asserts the `CloudTTSResponse` the robot received is
real 22050 Hz speech, not the placeholder tone — the anti-tone guard being the whole
point, since `ToneSynthesizer` emits the same rate and the same mono PCM16.

**Budget: exactly ONE `/audio/speech` request.** One stack boot, one session, one
`PLAY_OUTPUT`; there is no brain in this loop (`MOXIE_APP=echo`, and the runtime refuses
remote-chat while a telehealth session is open) and no ears (`MOXIE_STT=off`), so a green
run costs one gateway call. The count is asserted below rather than trusted.

Skips instantly without a gateway voice URL + key, without a broker, or without numpy.

    .venv/bin/python -m pytest sim/tests/test_live_telehealth_voice.py -q -s
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
MODEL = (os.environ.get("MOXIE_VOICE_MODEL") or "piper-amy").strip()

#: What the remote grown-up says. Ordinary, child-facing, long enough that a duration
#: check is meaningful and short enough to stay one cheap call.
OPERATOR_LINE = "Hi Sam, it is me. Are you ready for our chat today?"

live = pytest.mark.skipif(
    not (VOICE_BASE and KEY),
    reason="no gateway voice configured (set MOXIE_VOICE_BASE_URL + a key in mqtt/.env)")


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    """ONE boot, ONE telehealth session, ONE gateway voice call for the whole module."""
    if not (VOICE_BASE and KEY):
        pytest.skip("no gateway voice configured")
    if not S.broker_available():
        pytest.skip("no mosquitto binary and no runnable docker — cannot boot a broker")
    from virtual_moxie import VirtualMoxie
    logs = str(tmp_path_factory.mktemp("live-telehealth"))
    env = {"MOXIE_APP": "echo",                 # no brain in a puppet session, by design
           "MOXIE_TTS": "",                     # let build_synthesizer's precedence pick
           "MOXIE_VOICE_BASE_URL": VOICE_BASE,  #    the gateway voice
           "MOXIE_VOICE_FORMAT": "wav",
           "MOXIE_STT": "off",
           "MOXIE_CHILD_NICKNAME": "Sam"}
    with S.Stack(logs, env=env) as stack:
        voice_line = stack.supervisor.line_with("server voice enabled")
        print(f"\n[live-th] {voice_line}")
        vm = VirtualMoxie("127.0.0.1", stack.port, timeout=60.0, verbose=True)
        ok = vm.run_telehealth(f"http://127.0.0.1:{stack.supervisor.status_port}",
                               line=OPERATOR_LINE)
        log = stack.supervisor.text()
    return dict(ok=ok, vm=vm, voice_line=voice_line, log=log, errors=list(vm.errors))


@live
def test_the_operator_drives_the_body_through_the_assembled_appliance(session):
    """The wire, re-proven on the gateway-voice build: every step of the recovered
    session shape reached this robot, in order."""
    assert session["ok"], session["errors"]
    actions = [rec["action"] for rec in session["vm"].telehealth]
    assert actions == ["START_SESSION", "PLAY_OUTPUT", "INTERRUPT", "END_SESSION"], actions
    play = next(r for r in session["vm"].telehealth if r["action"] == "PLAY_OUTPUT")
    assert play["text"] == OPERATOR_LINE, play
    assert play["markup"].strip(), play


@live
def test_the_supervisor_assembled_the_gateway_voice_not_a_local_one(session):
    """`config.build_synthesizer()` precedence, as the appliance itself reports it at
    startup — and it must not have fallen through mid-session either."""
    line = session["voice_line"]
    assert "openai-voice" in line, line
    assert "standby:" in line, line              # FallbackSynthesizer, never bare
    assert "[voice] openai-voice failed" not in session["log"], (
        "the gateway voice fell back mid-session — the audio below is the standby's")


@live
def test_the_robot_played_real_speech_not_the_placeholder_tone(session):
    """The point of the whole file. `ToneSynthesizer` also emits 22050 Hz mono PCM16, so
    the rate is checked *and* the audio has to be broadband enough to be a voice."""
    spoke = session["vm"].spoke
    assert spoke and spoke["audio"], session["errors"]
    assert spoke["sample_rate"] == 22050, spoke["sample_rate"]   # the WAV header's rate
    assert spoke["channels"] == 1, spoke["channels"]
    flat = A.spectral_flatness(spoke["audio"])
    seconds = A.duration_s(spoke["audio"], spoke["sample_rate"])
    print(f"\n[live-th] 🔊 {len(spoke['audio'])} B @ {spoke['sample_rate']} Hz "
          f"({seconds:.2f}s) flatness={flat:.3e} floor={A.SPEECH_FLATNESS_FLOOR:.0e} "
          f"model={MODEL}")
    assert flat > A.SPEECH_FLATNESS_FLOOR, (
        f"flatness {flat:.3e} is tone-shaped — the operator's line was not spoken by the "
        "gateway voice")
    words = len(OPERATOR_LINE.split())
    assert seconds >= 0.15 * words, (
        f"{seconds:.2f}s of audio for {words} words — that is not the whole line")


@live
def test_the_session_spent_exactly_one_gateway_voice_call(session):
    """A puppet session has no brain and no ears; the only thing that may reach the
    gateway is the single `PLAY_OUTPUT`. `INTERRUPT` in particular must not re-synthesize
    (it carries no `output` at all), and a filler timer must never arm."""
    plays = [r for r in session["vm"].telehealth if r["action"] == "PLAY_OUTPUT"]
    assert len(plays) == 1, plays
    log = session["log"]
    assert "💬" not in log, "a brain turn ran during the telehealth session"
    assert "[stt]" not in log, "the ears were built despite MOXIE_STT=off"
