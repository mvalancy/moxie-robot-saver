"""
Honest ears (ai-seam.md §1 "What the ears refuse to hear"): Whisper's "Bye." on room tone is
never the child's word, a sound label alone is silence, and local Whisper runs its own VAD.

Whisper answers silence and room noise with words ("Bye.", "Thank you.", "you") or with
sound labels ("(machine whirring)", "[BLANK_AUDIO]"). On origin/dev the robot path published
that verbatim as the FINAL `zmqSTTResponse` and the robot took it as the child's turn, so a
child who only paused could lose the activity to a phantom "Bye." (the llm brain is taught
`<exit>` for a goodbye; K5's content-brain Goodbye, #312, accepts Whisper's lone "By.").
Pinned here:

* `SttSession`: a clip at digital silence or under 120 ms reaches no engine; sound labels are
  stripped; one of Whisper's silence phrases (`PHANTOM_CANON`, pinned as a set) is dropped
  when the clip is not loud and is quiet or short. A loud clip and a real sentence are kept.
* the runtime: a drop is still a FINAL (no speech, the utterance's uuid), one console note
  with the canon phrase and the numbers (never the transcript), and `/status` `stt_dropped`.
* `WhisperTranscriber` passes `vad_filter=True` and drops a segment Whisper rates as silence.
* `MOXIE_STT_PHANTOM_GATE=off` restores the ears byte for byte, local whisper included, and
  config.py reads every spelling of it the way stt.py does.
* every threshold (the digital-silence floor, the loud edge, whisper's no-speech cut) sits
  between two pinned clips, so moving it either way fails a test
  (sim/tools/ears_mutation_check.py breaks each one).

Levels are RMS as a fraction of int16 full scale. The thresholds come from the hosted page's
browser microphones (sim/web/mic.js) and are unverified against Moxie's microphone; these
tests pin the rule, not the right numbers for a robot. Hermetic: fake engines that count
their calls, a fake `faster_whisper` module, the real runtime over a fake transport or the
in-process robot loopback, every FINAL decoded with the committed zmqSTT pb2.

Red before green on origin/dev: `test_whispers_bye_on_silence_is_not_the_childs_word`
(SttSession returned 'Bye.' for 1.5 s of zeros, and the robot double's FINAL carried it),
`test_a_sound_label_alone_is_silence` ('(machine whirring)' published as the child's speech)
and `test_local_whisper_runs_its_own_vad` (no `vad_filter` keyword). The quiet-goodbye
loopback over the shipped content brain is in test_stt_wire.py.
"""
from __future__ import annotations

import re
import sys
import threading
import types

import pytest

from helpers_audio import silence_pcm, stt_frames, tone_pcm                 # noqa: E402
from helpers_runtime import (deliver, http_json, loopback, make_runtime,      # noqa: E402
                             parse_zmq_frame, status_server, toolkit_pb2)
from moxie_sdk import stt                                                     # noqa: E402
from moxie_sdk.stt import SttSession, Transcriber, VADState                   # noqa: E402
from moxie_sdk.store import JsonStore                                         # noqa: E402
from test_stt_wire import (DEV, EchoApp, RobotDouble, _HeldTimer, _connect,   # noqa: E402
                           _runtime, _zmq)

zmqSTT_pb2 = toolkit_pb2("embodied.perception.audio.zmqSTT_pb2")

#: Speech level ("a 300 Hz tone at 0.3 full scale", RMS 0.21) and room tone (RMS 0.004).
LOUD = {"amplitude": 0.3}
ROOM_TONE = {"rms": 0.004}

KNOBS = ("MOXIE_STT_PHANTOM_GATE", "MOXIE_STT_ROOM_TONE_RMS", "MOXIE_STT_MIN_SPEECH_MS")


@pytest.fixture(autouse=True)
def _default_knobs(monkeypatch):
    """The shipped defaults, whatever the developer's shell exports."""
    for name in KNOBS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def timers(monkeypatch):
    """The robot's one-second settle after a connect line, held until `fire()`."""
    _HeldTimer.pending = []
    monkeypatch.setattr(threading, "Timer", _HeldTimer)
    return _HeldTimer


class Ears(Transcriber):
    """A fake engine that always writes `text` (Whisper's habit on silence) and counts calls."""
    name = "fake-ears"

    def __init__(self, text="Bye."):
        self.text, self.calls = text, 0

    def transcribe(self, pcm, sample_rate=16000):
        self.calls += 1
        return self.text


def hear(text, pcm, **session_kw):
    """One utterance through a fresh `SttSession`: `(transcript, engine calls, session)`."""
    ears = Ears(text)
    session = SttSession(ears, **session_kw)
    assert session.feed(VADState.START_OF_SPEECH, pcm) is None
    return session.feed(VADState.END_OF_SPEECH, b""), ears.calls, session


def speak(rt, device_id, pcm, uuid):
    """Stream one utterance into the runtime as a robot's protobuf `events/zmq` frames
    (START, SPEECH per 200 ms, END) and return the FINAL it published, parsed."""
    for frame in stt_frames(pcm, uuid):
        rt.handle_zmq(device_id, frame)
    return parse_zmq_frame(_zmq(rt, device_id)[-1], zmqSTT_pb2.zmqSTTResponse)


def _notes_during(rt, action):
    """The console notes (`recent`) one action added."""
    before = len(rt.recent)
    action()
    return [r["text"] for r in list(rt.recent)[before:]]


# --------------------------------------------------------------------------- #
# The three reds of origin/dev
# --------------------------------------------------------------------------- #

def test_whispers_bye_on_silence_is_not_the_childs_word(timers, tmp_path):
    """1.5 s of digital silence and an engine that answers 'Bye.': on origin/dev SttSession
    returned 'Bye.' (stt.py:93) and the robot was handed it as the child's FINAL. Now no
    engine is asked at all, and the robot's FINAL has no speech and the utterance's uuid,
    so its turn still closes."""
    said, calls, _ = hear("Bye.", silence_pcm(1500))
    assert (said, calls) == ("", 0), f"the ears heard {said!r} in silence ({calls} calls)"

    robot = RobotDouble(DEV, stt_frames(silence_pcm(1500), "utt-silent"))
    rt = _runtime(tmp_path, ears=False)
    loopback(rt, robot)
    ears = Ears("Bye.")
    rt.set_transcriber(ears)
    _connect(rt)
    timers.fire()
    assert len(robot.heard) == 1, "the robot was left waiting for its FINAL"
    final = robot.heard[0]
    assert (final.type, final.uuid) == (final.FINAL, "utt-silent")
    assert final.speech == "", f"the robot was told the child said {final.speech!r}"
    assert ears.calls == 0


@pytest.mark.parametrize("level", [LOUD, ROOM_TONE], ids=["loud", "quiet"])
def test_a_sound_label_alone_is_silence(tmp_path, level):
    """'(machine whirring)' is Whisper naming a noise: on origin/dev it went to the robot
    verbatim as the child's speech. At any level it is now a FINAL with no speech."""
    rt, dev = make_runtime(EchoApp(), device_id=DEV, store=JsonStore(str(tmp_path)))
    ears = Ears("(machine whirring)")
    rt.set_transcriber(ears)
    final = speak(rt, dev, tone_pcm(800, **level), "utt-label")
    assert (final.type, final.uuid) == (final.FINAL, "utt-label")
    assert final.speech == "", f"the robot was told the child said {final.speech!r}"
    assert ears.calls == 1, "a clip with sound in it still goes to the engine"


class _Segment:
    """faster-whisper's `Segment`, as much of it as the transcriber reads."""

    def __init__(self, text, no_speech_prob):
        self.text, self.no_speech_prob = text, no_speech_prob


def fake_faster_whisper(monkeypatch, segments):
    """Install a `faster_whisper` module whose model records every `transcribe` keyword
    set and answers `segments`. Returns the list of recorded keyword dicts."""
    calls = []

    class WhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            return iter(list(segments)), None

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return calls


def test_local_whisper_runs_its_own_vad(monkeypatch):
    """Local whisper cuts the silence out before decoding (`vad_filter=True`, as the SIL
    STT service does, sim/stt/server.py) and drops a segment it rates as more likely
    silence than speech (no_speech_prob > 0.6). On origin/dev neither happened."""
    calls = fake_faster_whisper(monkeypatch, [_Segment(" Thank you.", 0.9),
                                              _Segment(" I like dogs.", 0.2)])
    whisper = stt.WhisperTranscriber()
    heard = whisper.transcribe(tone_pcm(800, **LOUD))
    assert calls and calls[0].get("vad_filter") is True, calls
    assert heard == "I like dogs.", heard
    assert calls[0]["language"] == "en" and calls[0]["beam_size"] == 1, "nothing else moved"


def test_local_whispers_silence_cut_sits_at_0_6(monkeypatch):
    """The cut is `no_speech_prob` above 0.6, on that number alone (stricter than Whisper's
    own rule, which also wants a low average log probability; ai-seam.md §1): 0.6 and 0.55
    are kept, 0.65 is cut. Moving the edge either way fails here."""
    fake_faster_whisper(monkeypatch, [_Segment("one", 0.55), _Segment("two", 0.6),
                                      _Segment("three", 0.65), _Segment("four", None)])
    assert stt.WhisperTranscriber().transcribe(tone_pcm(800, **LOUD)) == "one two four"


# --------------------------------------------------------------------------- #
# SttSession: what is dropped, what is kept
# --------------------------------------------------------------------------- #

#: Whisper's silence phrases as it writes them.
PHRASES = ["Bye.", "By.", "Bye bye.", "Goodbye.", "Thank you.", "you"]


@pytest.mark.parametrize("text", PHRASES)
def test_a_silence_phrase_is_dropped_on_room_tone_and_kept_at_speech_level(text):
    """The same transcript, three clips: digital silence reaches no engine; speech level
    (0.4 s and 0.8 s) is kept verbatim; room tone (0.6 s at RMS 0.004) is dropped and the
    drop is recorded with its reason, canon phrase, duration and level."""
    assert hear(text, silence_pcm(1500))[:2] == ("", 0)
    for ms in (400, 800):
        said, calls, session = hear(text, tone_pcm(ms, **LOUD))
        assert (said, calls) == (text, 1), (ms, said)
        assert session.last_drop is None
    said, calls, session = hear(text, tone_pcm(600, **ROOM_TONE))
    assert (said, calls) == ("", 1)
    drop = session.last_drop
    assert drop["reason"] == "quiet_short_hallucination"
    assert drop["phrase"] == stt.canon_phrase(text) and drop["phrase"] in stt.PHANTOM_CANON
    assert drop["ms"] == pytest.approx(600, abs=1)
    assert drop["rms"] == pytest.approx(0.004, abs=0.0002)
    assert session.last_stats[0] == pytest.approx(600, abs=1)


@pytest.mark.parametrize("ms,rms,kept,why", [
    (240, 0.3, True, "loud wins"),
    (240, 0.02, False, "short and not loud"),
    (600, 0.02, True, "neither quiet nor short"),
    (600, 0.004, False, "quiet"),
    (240, 0.055, True, "loud, just over the 0.05 edge"),
    (240, 0.045, False, "short and just under the loud edge"),
], ids=["240ms-0.3", "240ms-0.02", "600ms-0.02", "600ms-0.004", "240ms-0.055",
        "240ms-0.045"])
def test_the_boundary_table(ms, rms, kept, why):
    """The rule at its edges (levels as RMS): not loud (< 0.05) AND (quieter than room tone
    0.01 OR shorter than 250 ms) drops a canon phrase; anything else keeps it. The last two
    rows sit either side of the loud edge, so moving it either way fails here."""
    said, calls, session = hear("Bye.", tone_pcm(ms, rms=rms))
    assert calls == 1
    assert said == ("Bye." if kept else ""), why
    assert (session.last_drop is None) is kept, why


@pytest.mark.parametrize("rms,asked", [(0.0007, False), (0.0014, True)],
                         ids=["under-0.001", "over-0.001"])
def test_the_digital_silence_floor_sits_at_0_001(rms, asked):
    """The only drop made without asking an engine: a clip quieter than RMS 0.001 reaches
    none, and one just above it does (where room tone may still drop its 'Bye.'). Moving
    the floor either way fails here."""
    said, calls, session = hear("Bye.", tone_pcm(600, rms=rms))
    assert stt.audio_stats(tone_pcm(600, rms=rms))[1] == pytest.approx(rms, rel=0.03)
    assert (said, calls) == ("", int(asked))
    assert session.last_drop["reason"] == ("quiet_short_hallucination" if asked
                                           else "silence")


@pytest.mark.parametrize("text", [
    "I like dogs",                              # three or more words: a sentence
    "I don't want to play anymore, bye",        # contains a canon word, is not one
    "okay", "Okay.", "hmm", "Hmm.", "yes", "no", "uh",   # a child's real short answers
])
@pytest.mark.parametrize("ms", [200, 600], ids=["short", "long"])
def test_a_quiet_real_answer_is_kept(text, ms):
    """Room tone and a short clip drop only a canon phrase, word for word: a quiet
    sentence and a quiet 'okay' or 'hmm' are the child's, and are kept."""
    said, calls, session = hear(text, tone_pcm(ms, **ROOM_TONE))
    assert (said, calls) == (text, 1)
    assert session.last_drop is None


def test_the_phantom_canon_is_pinned_as_a_set():
    """OQ2 (owner question, default applied): this list and nothing else. Adding a word a
    child really answers with ('okay', 'yes', 'no', 'hmm', 'uh') must fail here."""
    assert stt.PHANTOM_CANON == frozenset({
        "bye", "by", "bye bye", "goodbye", "you", "thank you", "thanks",
        "thanks for watching", "the end", "subtitles by the amara org community"})
    for answer in ("okay", "ok", "yes", "yeah", "no", "hmm", "uh", "um", "hi", "moxie"):
        assert answer not in stt.PHANTOM_CANON


@pytest.mark.parametrize("text,phrase", [
    ("Bye.", "bye"), ("BYE!", "bye"), ("By.", "by"), ("Bye-bye!", "bye bye"),
    ("Bye bye.", "bye bye"), (" you", "you"), ("Thank you.", "thank you"),
    ("Thanks for watching!", "thanks for watching"), ("The end.", "the end"),
    ("Subtitles by the Amara.org community", "subtitles by the amara org community"),
    ("(sighs) Bye.", "bye"),
    ("Bye bye bye.", ""), ("Bye, Moxie!", ""), ("Thank you very much.", ""),
    ("okay", ""), ("", ""), ("[BLANK_AUDIO]", ""),
])
def test_canon_phrase_matches_the_whole_transcript_only(text, phrase):
    assert stt.canon_phrase(text) == phrase


# --------------------------------------------------------------------------- #
# clean_transcript, and the no-call floor for every engine
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw", ["(machine whirring)", "[BLANK_AUDIO]", "(silence)",
                                 "♪ ♪ ♪", "♪♪", " [Music] ", "(door slams) [BLANK_AUDIO]",
                                 "(a (nested) label)", "...", " \x00\t "])
def test_a_label_or_a_line_of_music_notes_is_no_words(raw):
    assert stt.clean_transcript(raw) == ""


@pytest.mark.parametrize("raw", ["(machine whirring)", "[BLANK_AUDIO]", "(silence)", "♪ ♪"])
@pytest.mark.parametrize("level", [LOUD, ROOM_TONE], ids=["loud", "quiet"])
def test_a_label_alone_is_dropped_at_any_level(raw, level):
    said, calls, session = hear(raw, tone_pcm(800, **level))
    assert (said, calls) == ("", 1)
    assert session.last_drop["reason"] == "label_only"
    assert session.last_drop["phrase"] == ""


def test_clean_transcript_keeps_the_words_and_tidies_them():
    assert stt.clean_transcript("(laughs) hi Moxie") == "hi Moxie"
    assert stt.clean_transcript("[BLANK_AUDIO] I like dogs (barking)") == "I like dogs"
    assert stt.clean_transcript("\x00hi\x07 there\x1b,\tMoxie\n") == "hi there , Moxie"
    # DEL and the C1 range, as the hosted ears strip them (transcribe.js cleanTranscript);
    # none of these is whitespace to str.split(), so only the strip removes them
    assert stt.clean_transcript("hi\x7fthere\x80Moxie\x9b!\x9f") == "hi there Moxie !"
    assert stt.clean_transcript("  tell   me  a story  ") == "tell me a story"
    assert stt.clean_transcript("7") == "7", "a digit is an answer"
    # through the session, a loud clip: the child's words, without the label
    assert hear("(laughs) hi Moxie", tone_pcm(800, **LOUD))[0] == "hi Moxie"


class _Client:
    """The OpenAI SDK's `client.audio.transcriptions.create`, recording each request."""

    def __init__(self):
        self.calls = []
        self.audio = self

    @property
    def transcriptions(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return {"text": "Bye."}


def _gateway(client):
    return stt.OpenAITranscriber("https://gateway.example/v1", "sk-test", client=client,
                                 sleep=lambda s: None)


def test_digital_silence_and_a_sub_120_ms_clip_reach_no_engine(monkeypatch):
    """No engine is asked, whichever it is: a fake, local whisper (a fake faster_whisper),
    the gateway (a fake client), and the gateway with whisper standing by."""
    whisper_calls = fake_faster_whisper(monkeypatch, [_Segment(" Bye.", 0.1)])
    ears, client, standby_client = Ears("Bye."), _Client(), _Client()
    engines = [ears, stt.WhisperTranscriber(), _gateway(client),
               stt.FallbackTranscriber(_gateway(standby_client), stt.WhisperTranscriber(),
                                       log=lambda m: None)]
    for clip in (silence_pcm(1500), tone_pcm(100, **LOUD), tone_pcm(119, **LOUD)):
        for engine in engines:
            session = SttSession(engine)
            session.feed(VADState.START_OF_SPEECH, clip)
            assert session.feed(VADState.END_OF_SPEECH, b"") == ""
            assert session.last_drop["reason"] in ("silence", "too_short")
    assert (ears.calls, whisper_calls, client.calls, standby_client.calls) == (0, [], [], [])
    # 120 ms of speech is a clip: it goes up
    assert hear("hi", tone_pcm(120, **LOUD))[:2] == ("hi", 1)


def test_the_gateway_engines_own_floor_still_answers_without_a_request():
    """`OpenAITranscriber.MIN_MS` stays (a direct caller has no session in front of it),
    and the session's floor is the same 120 ms."""
    client = _Client()
    assert _gateway(client).transcribe(tone_pcm(100, **LOUD)) == ""
    assert client.calls == []
    assert stt.MIN_UTTERANCE_MS == stt.OpenAITranscriber.MIN_MS == 120


def test_audio_stats_reads_duration_and_level():
    ms, rms = stt.audio_stats(tone_pcm(500, rms=0.02))
    assert ms == pytest.approx(500) and rms == pytest.approx(0.02, rel=0.01)
    assert stt.audio_stats(silence_pcm(250)) == (pytest.approx(250), 0.0)
    assert stt.audio_stats(b"") == (0.0, 0.0)
    assert stt.audio_stats(b"\xff\x7f\x00", 16000)[1] == pytest.approx(32767 / 32768)
    assert stt.audio_stats(b"\x00\x00" * 8000, 8000)[0] == pytest.approx(1000), "a rate"


# --------------------------------------------------------------------------- #
# The runtime: still a FINAL, one note, a counter
# --------------------------------------------------------------------------- #

def test_each_drop_is_one_note_with_the_numbers_and_counts_in_status(tmp_path, capsys):
    """Every drop: a FINAL with no speech and the uuid; exactly one console note naming the
    reason, the canon phrase (from the fixed list), the duration and the level, never the
    audio and never the engine's text, and the same words once in the supervisor's log;
    `/status` `stt_dropped` counts them. A kept turn, and an engine that heard nothing,
    leave the counter alone and are noted as before."""
    rt, dev = make_runtime(EchoApp(), device_id=DEV, store=JsonStore(str(tmp_path)))
    ears = Ears("Thank you.")
    rt.set_transcriber(ears)
    assert rt.status_snapshot()["robots"][0]["stt_dropped"] == 0
    capsys.readouterr()

    def logged():
        return [ln for ln in capsys.readouterr().out.splitlines() if "heard nothing" in ln]

    cases = [
        ("Thank you.", tone_pcm(600, **ROOM_TONE),
         "👂 heard nothing: dropped a phantom 'thank you' (0.60 s, level 0.004)"),
        ("(someone whispers a secret)", tone_pcm(600, **ROOM_TONE),
         "👂 heard nothing: only a sound label, no words (0.60 s, level 0.004)"),
        ("Bye.", silence_pcm(1000),
         "👂 heard nothing: digital silence, not sent to the ears (1.00 s, level 0.000)"),
        ("Bye.", tone_pcm(100, **LOUD),
         "👂 heard nothing: too short to be a word, not sent to the ears (0.10 s, level 0.212)"),
    ]
    for i, (text, pcm, line) in enumerate(cases, 1):
        ears.text = text
        finals = []
        notes = _notes_during(rt, lambda: finals.append(speak(rt, dev, pcm, f"utt-{i}")))
        assert (finals[0].type, finals[0].speech, finals[0].uuid) == \
            (finals[0].FINAL, "", f"utt-{i}")
        assert notes == [line], notes
        assert "secret" not in notes[0] and "Thank you." not in notes[0]
        assert logged() == [f"[runtime] 👂 {dev} {line[len('👂 '):]}"]
        assert rt.status_snapshot()["robots"][0]["stt_dropped"] == i

    ears.text = "I like dogs"
    notes = _notes_during(rt, lambda: speak(rt, dev, tone_pcm(800, **LOUD), "utt-kept"))
    assert notes == ["👂 heard: 'I like dogs'"]
    # the engine heard nothing: an empty transcription, as before the gate, not a drop
    for nothing in ("", "  "):
        ears.text = nothing
        notes = _notes_during(rt, lambda: speak(rt, dev, tone_pcm(800, **LOUD), "utt-none"))
        assert notes == ["👂 heard: ''"], notes
    assert logged() == []
    assert rt.status_snapshot()["robots"][0]["stt_dropped"] == 4
    assert http_json(f"{status_server(rt)}/status")["robots"][0]["stt_dropped"] == 4


@pytest.mark.parametrize("nothing", ["", "  ", None])
def test_an_engine_that_heard_nothing_is_not_a_drop(nothing):
    """An empty transcription is the engine's answer, not something the ears refused: no
    reason is recorded for it (it is not 'only a sound label') and it is still ''."""
    said, calls, session = hear(nothing, tone_pcm(800, **LOUD))
    assert (said, calls, session.last_drop) == ("", 1, None)
    assert stt.phantom_reason(tone_pcm(800, **LOUD), 16000, nothing) == ""


def test_the_drop_count_lives_on_the_robots_record(timers, tmp_path):
    """`stt_dropped` is in memory on the robot's record: a second connect line with no
    leave in between keeps it (the record, like the conversation, survives the blip), and
    the broker saying the robot left starts it again at 0."""
    rt = _runtime(tmp_path)
    rt.set_transcriber(Ears("Bye."))
    _connect(rt)
    timers.fire()
    speak(rt, DEV, tone_pcm(600, **ROOM_TONE), "utt-1")
    assert rt.status_snapshot()["robots"][0]["stt_dropped"] == 1
    _connect(rt)
    timers.fire()
    assert rt.status_snapshot()["robots"][0]["stt_dropped"] == 1
    deliver(rt, "$SYS/broker/log/N",
            f"1759900030: Client {DEV} has exceeded timeout, disconnecting.")
    _connect(rt)
    timers.fire()
    assert rt.status_snapshot()["robots"][0]["stt_dropped"] == 0


def test_a_listening_session_that_cannot_be_built_still_answers_the_robot(tmp_path,
                                                                          monkeypatch):
    """Building the session is inside `feed_stt`'s guard, with the engine call: if it
    raises, the robot still gets its FINAL (no speech, `error_code` 66, the utterance's
    uuid) and is never left waiting on a turn that ended."""
    rt, dev = make_runtime(EchoApp(), device_id=DEV, store=JsonStore(str(tmp_path)))
    rt.set_transcriber(Ears())

    def no_session(device_id):
        raise RuntimeError("no session")

    monkeypatch.setattr(rt, "_stt_session", no_session)
    final = speak(rt, dev, tone_pcm(600, **LOUD), "utt-broken")
    assert (final.type, final.speech, final.uuid, final.error_code) == \
        (final.FINAL, "", "utt-broken", stt.STT_ERROR_CODE)


def test_a_drop_never_breaks_the_utterance_that_follows(tmp_path):
    """Per robot, per utterance: a dropped phantom leaves the next utterance's audio,
    uuid and transcript untouched."""
    rt, dev = make_runtime(EchoApp(), device_id=DEV, store=JsonStore(str(tmp_path)))
    ears = Ears("Bye.")
    rt.set_transcriber(ears)
    assert speak(rt, dev, tone_pcm(600, **ROOM_TONE), "utt-a").speech == ""
    final = speak(rt, dev, tone_pcm(600, **LOUD), "utt-b")
    assert (final.speech, final.uuid) == ("Bye.", "utt-b")
    assert ears.calls == 2


# --------------------------------------------------------------------------- #
# The kill switch, and where the knobs are read
# --------------------------------------------------------------------------- #

def test_the_kill_switch_restores_the_ears_byte_for_byte(monkeypatch, tmp_path):
    """`MOXIE_STT_PHANTOM_GATE=off`: every clip goes to the engine and its text comes back
    as origin/dev returned it (`(text or "").strip()`); the runtime publishes the quiet
    'Bye.', notes it as heard, and counts nothing."""
    monkeypatch.setenv("MOXIE_STT_PHANTOM_GATE", "off")
    for text, pcm in [("Bye.", silence_pcm(1500)), ("Bye.", tone_pcm(600, **ROOM_TONE)),
                      ("  (machine whirring) ", tone_pcm(600, **LOUD)),
                      (" Thank you. ", b"\x01\x02"), ("", tone_pcm(600, **LOUD))]:
        said, calls, session = hear(text, pcm)
        assert (said, calls) == (text.strip(), 1), (text, said)
        assert session.last_drop is None and session.last_stats is None

    rt, dev = make_runtime(EchoApp(), device_id=DEV, store=JsonStore(str(tmp_path)))
    rt.set_transcriber(Ears("Bye."))
    final = []
    notes = _notes_during(rt, lambda: final.append(
        speak(rt, dev, tone_pcm(600, **ROOM_TONE), "utt-off")))
    assert (final[0].speech, final[0].uuid) == ("Bye.", "utt-off")
    assert notes == ["👂 heard: 'Bye.'"]
    assert rt.status_snapshot()["robots"][0]["stt_dropped"] == 0


def test_the_kill_switch_turns_off_local_whispers_vad_too(monkeypatch):
    """Off means every part of the honest ears, the half inside local whisper included: the
    model is called exactly as origin/dev called it (no `vad_filter`) and a segment it rates
    as silence is kept. So an operator who suspects the voice detector of eating soft speech
    can rule it out with the one documented switch."""
    monkeypatch.setenv("MOXIE_STT_PHANTOM_GATE", "off")
    calls = fake_faster_whisper(monkeypatch, [_Segment(" Thank you.", 0.9),
                                              _Segment(" I like dogs.", 0.2)])
    whisper = stt.WhisperTranscriber()
    assert whisper.phantom_gate is False
    assert whisper.transcribe(tone_pcm(800, **LOUD)) == "Thank you.  I like dogs."
    assert calls == [{"language": "en", "beam_size": 1}], calls


def test_an_explicit_setting_wins_over_the_environment(monkeypatch):
    """`SttSession(phantom_gate=, room_tone_rms=, min_speech_ms=)` and
    `WhisperTranscriber(phantom_gate=)` override the knobs (an SDK user's own ears)."""
    quiet = tone_pcm(600, **ROOM_TONE)
    assert hear("Bye.", quiet, phantom_gate=False)[0] == "Bye."
    assert hear("Bye.", tone_pcm(600, rms=0.02), room_tone_rms=0.03)[0] == ""
    assert hear("Bye.", tone_pcm(600, rms=0.02), min_speech_ms=700)[0] == ""
    monkeypatch.setenv("MOXIE_STT_PHANTOM_GATE", "off")
    assert hear("Bye.", quiet, phantom_gate=True)[0] == ""
    calls = fake_faster_whisper(monkeypatch, [_Segment(" Thank you.", 0.9)])
    assert stt.WhisperTranscriber(phantom_gate=True).transcribe(quiet) == ""
    assert calls[-1].get("vad_filter") is True
    monkeypatch.delenv("MOXIE_STT_PHANTOM_GATE")
    assert stt.WhisperTranscriber(phantom_gate=False).transcribe(quiet) == "Thank you."
    assert "vad_filter" not in calls[-1]


GATE_VALUES = [
    ("off", False), ("OFF", False), ("0", False), ("false", False), ("no", False),
    (" off ", False), ("\toff\n", False),
    ("", True), ("  ", True), ("\t", True), ("on", True), ("1", True), ("yes", True),
    (" On ", True), ("maybe", True),
]


@pytest.mark.parametrize("value,on", GATE_VALUES)
def test_only_an_explicit_off_turns_the_gate_off(monkeypatch, value, on):
    """An empty or blank value (a copied `.env.example`, a compose `${VAR:-}`) keeps the
    gate on, and config.py's name for the knob reads every value the same way."""
    from helpers_runtime import reload_config
    monkeypatch.setenv("MOXIE_STT_PHANTOM_GATE", value)
    assert stt.ears_knobs()["phantom_gate"] is on
    assert SttSession(Ears()).phantom_gate is on
    assert reload_config(monkeypatch, MOXIE_STT_PHANTOM_GATE=value).STT_PHANTOM_GATE is on


def test_the_knobs_move_the_thresholds_and_config_names_the_same_values(monkeypatch):
    """Both knobs are live: raise room tone above the clip and a 600 ms 'Bye.' at RMS 0.02
    drops; raise the shortest word above 600 ms and it drops too. A value that is not a
    number keeps the default, as config.py's `_env_float` does for the same variable."""
    from helpers_runtime import reload_config
    clip = tone_pcm(600, rms=0.02)
    assert hear("Bye.", clip)[0] == "Bye."
    monkeypatch.setenv("MOXIE_STT_ROOM_TONE_RMS", "0.03")
    assert hear("Bye.", clip)[0] == ""
    monkeypatch.setenv("MOXIE_STT_ROOM_TONE_RMS", "0.001")
    monkeypatch.setenv("MOXIE_STT_MIN_SPEECH_MS", "700")
    assert hear("Bye.", clip)[0] == ""
    assert hear("Bye.", tone_pcm(600, **LOUD))[0] == "Bye.", "a loud clip is never dropped"

    for env in [{}, {"MOXIE_STT_ROOM_TONE_RMS": "0.02", "MOXIE_STT_MIN_SPEECH_MS": "300",
                     "MOXIE_STT_PHANTOM_GATE": "off"},
                {"MOXIE_STT_ROOM_TONE_RMS": "loud", "MOXIE_STT_MIN_SPEECH_MS": "a while"}]:
        for name in KNOBS:
            monkeypatch.delenv(name, raising=False)
        cfg = reload_config(monkeypatch, **env)
        knobs = stt.ears_knobs()
        assert (cfg.STT_PHANTOM_GATE, cfg.STT_ROOM_TONE_RMS, cfg.STT_MIN_SPEECH_MS) == \
            (knobs["phantom_gate"], knobs["room_tone_rms"], knobs["min_speech_ms"]), env
    for name in KNOBS:
        monkeypatch.delenv(name, raising=False)
    cfg = reload_config(monkeypatch)
    assert (cfg.STT_PHANTOM_GATE, cfg.STT_ROOM_TONE_RMS, cfg.STT_MIN_SPEECH_MS) == \
        (True, stt.ROOM_TONE_RMS, stt.MIN_SPEECH_MS) == (True, 0.01, 250.0)


def test_describe_drop_says_only_fixed_words_and_numbers():
    """The note's whole vocabulary: a reason sentence, a canon phrase, two numbers."""
    pattern = re.compile(r"(dropped a phantom '[a-z ]+'|digital silence, not sent to the "
                         r"ears|too short to be a word, not sent to the ears|only a sound "
                         r"label, no words) \(\d+\.\d\d s, level \d\.\d\d\d\)")
    for reason in ("quiet_short_hallucination", "silence", "too_short", "label_only"):
        line = stt.describe_drop({"reason": reason, "phrase": "bye", "ms": 612.3,
                                  "rms": 0.00412})
        assert pattern.fullmatch(line), line
