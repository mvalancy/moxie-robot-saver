"""
STT seam (AI-seam §1) — turn the robot's streamed mic audio into a recognized
utterance. Transport-free + pluggable: a `Transcriber` (Whisper, or any engine, or a
Deepgram-shaped proxy) behind a small interface, and an `SttSession` that accumulates
`zmqSTT` audio frames by VAD state and emits the utterance on END_OF_SPEECH.

Wire shapes verbatim from embodied/perception/audio/zmqSTT.proto:
  zmqSTTRequest { VADState vad; bytes audio_content; string uuid }   VAD: UNKNOWN=0,
      START_OF_SPEECH=1, SPEECH=2, END_OF_SPEECH=3
  zmqSTTResponse { ResponseType type (PARTIAL=0/FINAL=1); string speech; float
      confidence; string uuid; ... }
"""
from __future__ import annotations
from enum import IntEnum
from typing import Optional


class VADState(IntEnum):
    UNKNOWN = 0
    START_OF_SPEECH = 1
    SPEECH = 2
    END_OF_SPEECH = 3


class Transcriber:
    """Any speech-to-text engine. Override `transcribe`."""
    name = "transcriber"

    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        raise NotImplementedError

    @classmethod
    def available(cls) -> bool:
        return True


class SttSession:
    """Accumulate one utterance's audio across VAD-tagged frames, then transcribe.

    feed(vad, audio) returns None while speech is ongoing, and the final transcript
    string when END_OF_SPEECH arrives (then resets for the next utterance)."""

    def __init__(self, transcriber: Transcriber, sample_rate: int = 16000):
        self._t = transcriber
        self._sr = sample_rate
        self._buf = bytearray()

    def reset(self) -> None:
        self._buf = bytearray()

    def feed(self, vad, audio: bytes = b"") -> Optional[str]:
        vad = VADState(int(vad))
        if vad == VADState.START_OF_SPEECH:
            self._buf = bytearray(audio or b"")
            return None
        if vad in (VADState.SPEECH, VADState.UNKNOWN):
            if audio:
                self._buf.extend(audio)
            return None
        if vad == VADState.END_OF_SPEECH:
            if audio:
                self._buf.extend(audio)
            pcm = bytes(self._buf)
            self.reset()
            if not pcm:
                return ""
            return (self._t.transcribe(pcm, self._sr) or "").strip()
        return None


class WhisperTranscriber(Transcriber):
    """Local STT via faster-whisper (CPU/GPU). Imported lazily so the SDK has no hard
    dependency; `available()` is False when it (or numpy) isn't installed."""
    name = "faster-whisper"

    def __init__(self, model: str = "base.en", device: str = "auto",
                 compute_type: str = "int8"):
        from faster_whisper import WhisperModel   # lazy
        self._model = WhisperModel(model, device=device, compute_type=compute_type)

    @classmethod
    def available(cls) -> bool:
        try:
            import faster_whisper  # noqa: F401
            import numpy  # noqa: F401
            return True
        except Exception:
            return False

    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        import numpy as np
        # 16-bit little-endian PCM → float32 in [-1, 1]
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(audio, language="en", beam_size=1)
        return " ".join(s.text for s in segments).strip()


def build_stt_response(uuid: str, speech: str, *, final: bool = True,
                       confidence: float = 1.0) -> dict:
    """A zmqSTTResponse (JSON) a revival server publishes back after transcription."""
    return {"type": "FINAL" if final else "PARTIAL", "speech": speech,
            "confidence": confidence, "uuid": uuid}
