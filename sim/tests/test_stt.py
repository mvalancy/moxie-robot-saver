"""
STT seam tests (M3) — the VAD accumulator + transcriber interface + response encoder.
Pure (no audio libs); the Whisper backend is exercised only for availability/skip.
"""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.stt import (  # noqa: E402
    VADState, Transcriber, SttSession, WhisperTranscriber, build_stt_response,
)


class _FakeTranscriber(Transcriber):
    def __init__(self):
        self.got = None

    def transcribe(self, pcm, sample_rate=16000):
        self.got = pcm
        return f"heard {len(pcm)} bytes"


def test_vad_states_match_proto():
    assert VADState.START_OF_SPEECH == 1
    assert VADState.SPEECH == 2
    assert VADState.END_OF_SPEECH == 3


def test_accumulates_until_end_of_speech():
    t = _FakeTranscriber()
    s = SttSession(t)
    assert s.feed(VADState.START_OF_SPEECH, b"aa") is None
    assert s.feed(VADState.SPEECH, b"bb") is None
    assert s.feed(VADState.SPEECH, b"cc") is None
    out = s.feed(VADState.END_OF_SPEECH, b"dd")
    assert out == "heard 8 bytes"
    assert t.got == b"aabbccdd"        # everything concatenated, in order


def test_int_vad_values_accepted():
    s = SttSession(_FakeTranscriber())
    assert s.feed(1, b"x") is None     # START_OF_SPEECH as int
    assert s.feed(3, b"y") == "heard 2 bytes"


def test_new_utterance_resets_buffer():
    t = _FakeTranscriber()
    s = SttSession(t)
    s.feed(VADState.START_OF_SPEECH, b"first")
    s.feed(VADState.END_OF_SPEECH, b"")
    # a fresh utterance must not carry the previous audio
    s.feed(VADState.START_OF_SPEECH, b"NEW")
    s.feed(VADState.END_OF_SPEECH, b"")
    assert t.got == b"NEW"


def test_empty_utterance_yields_empty_string():
    s = SttSession(_FakeTranscriber())
    assert s.feed(VADState.END_OF_SPEECH, b"") == ""


def test_response_encoder_shape():
    r = build_stt_response("u-1", "hello moxie")
    assert r == {"type": "FINAL", "speech": "hello moxie",
                 "confidence": 1.0, "uuid": "u-1"}
    assert build_stt_response("u", "hi", final=False)["type"] == "PARTIAL"


def test_whisper_availability_is_boolean():
    # no hard dep — just reports whether faster-whisper is installed
    assert isinstance(WhisperTranscriber.available(), bool)
