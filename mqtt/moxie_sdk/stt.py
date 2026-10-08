"""
STT seam (AI-seam §1) — turn the robot's streamed mic audio into a recognized
utterance. Transport-free + pluggable: a `Transcriber` (Whisper, or any engine, or a
Deepgram-shaped proxy) behind a small interface, and an `SttSession` that accumulates
`zmqSTT` audio frames by VAD state and emits the utterance on END_OF_SPEECH.

Two first-class engines: `WhisperTranscriber` (local faster-whisper — nothing leaves the
house) and `OpenAITranscriber` (an OpenAI-compatible `/audio/transcriptions` gateway, for
hosted deployments with no room for a model). `FallbackTranscriber` puts one behind the
other with the voice path's latch-and-report-once contract.

Wire shapes verbatim from embodied/perception/audio/zmqSTT.proto:
  zmqSTTRequest { VADState vad; bytes audio_content; string uuid }   VAD: UNKNOWN=0,
      START_OF_SPEECH=1, SPEECH=2, END_OF_SPEECH=3
  zmqSTTResponse { ResponseType type (PARTIAL=0/FINAL=1); string speech; float
      confidence; string uuid; ... }
and from embodied/logging/Log.proto:
  ProtoSubscribe { uint64 timestamp = 1; repeated string protos = 2 }

Both directions of `commands/zmq` / `events/zmq` are one ZMQ bus frame joined as
`b'<proto.full_name>:' + protobuf_bytes` (robot-ipc-protocol.md "Framing"). The robot
streams its microphone only after the cloud asks with a `ProtoSubscribe` naming
`zmqSTTRequest`, and reads the transcript back as a `zmqSTTResponse` in the same framing
(mqtt-and-conversation.md §3.4, §4.3). The encoders below write that framing with no
protobuf runtime, mirroring the reader `decode_zmq_stt_frame`; the committed
`tools/robot-toolkit` pb2 files are the oracle the tests check them against.
"""
from __future__ import annotations
import struct
import time
from enum import IntEnum
from typing import Iterable, Optional

#: Descriptor full names: the ZMQ bus routes by these strings (robot-ipc-protocol.md).
ZMQ_STT_REQUEST = "embodied.perception.audio.zmqSTTRequest"
ZMQ_STT_RESPONSE = "embodied.perception.audio.zmqSTTResponse"
PROTO_SUBSCRIBE = "embodied.logging.ProtoSubscribe"


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

    def describe(self) -> str:
        """One line for a startup log — which ears are these, really."""
        return self.name

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
    """Local STT via faster-whisper (CPU/GPU), imported lazily; `available()` is False
    without it or numpy. `MOXIE_STT=whisper` selects it even with a gateway configured."""
    name = "faster-whisper"

    def __init__(self, model: str = "base.en", device: str = "auto",
                 compute_type: str = "int8"):
        from faster_whisper import WhisperModel   # lazy
        self.model = model                        # public: a console model picker reads it
        self._model = WhisperModel(model, device=device, compute_type=compute_type)

    def describe(self) -> str:
        return f"{self.name} ({self.model})"

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


class SttServerError(RuntimeError):
    """A transcription endpoint answered with something that is not a transcript. Not
    retried (unlike 429/5xx); `FallbackTranscriber` catches it."""


def wav_bytes(pcm: bytes, sample_rate: int = 16000, *, channels: int = 1,
              sample_width: int = 2) -> bytes:
    """16-bit PCM → a RIFF/WAVE file in memory (the upload needs a file; the mic gives
    headerless frames). The header carries the true rate, or the audio pitch-shifts."""
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(int(channels))
        w.setsampwidth(int(sample_width))
        w.setframerate(int(sample_rate))
        w.writeframes(pcm or b"")
    return buf.getvalue()


def transcript_text(resp) -> str:
    """The text out of an `/audio/transcriptions` reply (SDK object, dict, or plain
    string), stripped. Anything without a `text` is an `SttServerError`."""
    text = getattr(resp, "text", None)
    if text is None and isinstance(resp, dict):
        text = resp.get("text")
    if text is None and isinstance(resp, (str, bytes)):
        text = resp.decode("utf-8", "replace") if isinstance(resp, bytes) else resp
    if text is None:
        raise SttServerError(
            f"the STT server returned no transcript (got {type(resp).__name__})")
    return str(text).strip()


class OpenAITranscriber(Transcriber):
    """Cloud ears via an OpenAI-compatible `/audio/transcriptions` endpoint (same host,
    key and rate limits as the brain and voice). Reply shape: `{"text": ...}`.

    openai is imported lazily; `client=`, `pacer=`, `sleep=` are test seams. 429/5xx are
    retried by the shared `chat.call_with_backoff` + `Pacer`.
    """
    name = "openai-stt"

    #: Shortest utterance worth a request (VAD closes on breaths and door slams).
    MIN_MS = 120

    #: Seconds one transcription request may take (config: MOXIE_STT_TIMEOUT_S). The
    #: transcript is produced on the broker thread, so this sits well inside the broker's
    #: keepalive drop; the SDK's own default was 600 s. Chosen, not measured.
    TIMEOUT_S = 12.0

    def __init__(self, base_url: str, api_key: str, model: str = "stt-whisper", *,
                 client=None, max_retries: int = 4, pacer=None, sleep=time.sleep,
                 min_ms: int = MIN_MS, language: Optional[str] = None,
                 timeout_s: Optional[float] = None):
        self._timeout_s = self.TIMEOUT_S if timeout_s is None else float(timeout_s)
        if client is None:
            from openai import OpenAI      # lazy — the module imports without openai
            from .chat import client_timeout
            client = OpenAI(base_url=base_url, api_key=api_key or "sk-local",
                            max_retries=0, timeout=client_timeout(self._timeout_s))
        from .chat import Pacer
        self._client = client
        #: Public: the console model picker reads it.
        self.model = model
        self.base_url = base_url
        self._language = language
        # Injectable (Pacer/backoff bind `time.sleep` early) so tests can back off instantly.
        self._pacer = pacer if pacer is not None else Pacer()
        self._sleep = sleep
        self._max_retries = max_retries
        self._min_ms = int(min_ms)

    def describe(self) -> str:
        return f"{self.name} ({self.model})"

    @classmethod
    def available(cls, base_url: str = "") -> bool:
        """True when the openai SDK is importable and an endpoint is configured."""
        if not (base_url or "").strip():
            return False
        try:
            import openai  # noqa: F401
            return True
        except Exception:
            return False

    def _too_short(self, pcm: bytes, sample_rate: int) -> bool:
        ms = (len(pcm or b"") / 2.0) / float(sample_rate or 1) * 1000.0
        return ms < self._min_ms

    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        from .chat import call_with_backoff
        if not pcm or self._too_short(pcm, sample_rate):
            return ""                    # no audio → no request, no cost, no latency
        wav = wav_bytes(pcm, sample_rate)

        def _once():
            import io
            # A fresh stream per attempt: a retry must not re-send a consumed BytesIO.
            kw = {"language": self._language} if self._language else {}
            return self._client.audio.transcriptions.create(
                model=self.model,
                file=("utterance.wav", io.BytesIO(wav), "audio/wav"),
                response_format="json", **kw)

        resp = call_with_backoff(_once, max_retries=self._max_retries,
                                 pacer=self._pacer, sleep=self._sleep,
                                 deadline_s=self._timeout_s)
        return transcript_text(resp)


class NullTranscriber(Transcriber):
    """The bottom rung: hears nothing and returns "" (the standby when no local whisper)."""
    name = "no-ears"

    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        return ""


class FallbackTranscriber(Transcriber):
    """Primary ears with a standby — the STT twin of `tts.py::FallbackSynthesizer`: the
    first failure is reported once and latches the standby, so a dead gateway does not
    cost every utterance its timeout. The latch is not for the rest of the run: after
    `retry_s` (config: MOXIE_ENGINE_RETRY_S) the next utterance tries the primary again,
    and an answer clears the latch with one recovery line — in the default image the
    standby is `NullTranscriber`, so a latch that never let go left Moxie deaf until
    someone restarted the supervisor."""
    name = "fallback"

    #: Seconds a latched standby holds before the next call tries the primary again.
    #: 0 = try the primary on every call (no latch). Chosen, not measured.
    RETRY_S = 60.0

    def __init__(self, primary: Transcriber, standby: Transcriber, *, log=None,
                 retry_s: Optional[float] = None, clock=time.time):
        self._primary, self._standby = primary, standby
        self._log = log if log is not None else _warn
        self._retry_s = max(0.0, float(self.RETRY_S if retry_s is None else retry_s))
        self._clock = clock                     # wall clock: `describe()` names the time
        self.failed = False
        self.failed_at: Optional[float] = None  # when the latch (last) closed

    @property
    def engine(self) -> Transcriber:
        """The engine that is doing the hearing right now."""
        return self._standby if self.failed else self._primary

    @property
    def engine_name(self) -> str:
        return self.engine.name

    def retry_at(self) -> Optional[float]:
        """When the primary is tried again (wall clock), or None while it is healthy."""
        return None if not self.failed else self.failed_at + self._retry_s

    def _retry_due(self) -> bool:
        return self.failed and self._clock() >= self.retry_at()

    def describe(self) -> str:
        if self.failed:
            return (f"{self._standby.describe()} (standby since {_hhmm(self.failed_at)} — "
                    f"{self._primary.name} failed; retrying the primary at "
                    f"{_hhmm(self.retry_at())})")
        return f"{self._primary.describe()} (standby: {self._standby.describe()})"

    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        if not self.failed or self._retry_due():
            try:
                text = self._primary.transcribe(pcm, sample_rate)
            except Exception as exc:            # noqa: BLE001 — any failure downgrades
                first = not self.failed
                self.failed, self.failed_at = True, self._clock()
                if first:
                    self._log(f"[stt] {self._primary.name} failed "
                              f"({type(exc).__name__}: {exc}); hearing with "
                              f"{self._standby.name} until it answers again (next try "
                              f"in {self._retry_s:g}s)")
                else:
                    self._log(f"[stt] {self._primary.name} still failing "
                              f"({type(exc).__name__}); hearing with "
                              f"{self._standby.name}, next try in {self._retry_s:g}s")
            else:
                if self.failed:
                    self.failed, self.failed_at = False, None
                    self._log(f"[stt] {self._primary.name} is back; hearing with it again")
                return text
        return self._standby.transcribe(pcm, sample_rate)


def _hhmm(t: Optional[float]) -> str:
    """A wall-clock instant as `HH:MM` for a startup/status line."""
    return time.strftime("%H:%M", time.localtime(t or 0))


def _warn(message: str) -> None:
    print(message, flush=True)


def make_openai_transcriber(base_url: str, api_key: str, model: str = "stt-whisper",
                            **kw) -> Optional[Transcriber]:
    """An `OpenAITranscriber` when an endpoint is configured, else None (mirrors
    `tts.py::make_voice_synthesizer`, and is the seam the config tests stub)."""
    if not (base_url or "").strip():
        return None
    return OpenAITranscriber(base_url, api_key, model=model, **kw)


def _read_varint(b: bytes, i: int):
    shift = 0
    val = 0
    while True:
        byte = b[i]
        i += 1
        val |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return val, i
        shift += 7


def decode_zmq_stt_frame(payload):
    """Decode a real robot's events/zmq frame `b'<full_name>:' + zmqSTTRequest_bytes`
    into {vad, audio, uuid}. Minimal, dependency-free protobuf reader for the three
    fields we need (vad=2 varint, audio_content=3 bytes, uuid=4 string); returns None
    if it isn't a zmqSTTRequest frame. Field numbers per zmqSTT.proto."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8", "replace")
    sep = payload.find(b":")
    if sep < 0 or not payload[:sep].endswith(b"zmqSTTRequest"):
        return None
    data = payload[sep + 1:]
    out = {"vad": 0, "audio": b"", "uuid": ""}
    i, n = 0, len(data)
    try:
        while i < n:
            tag, i = _read_varint(data, i)
            field, wt = tag >> 3, tag & 7
            if wt == 0:                        # varint
                val, i = _read_varint(data, i)
                if field == 2:
                    out["vad"] = val
            elif wt == 2:                      # length-delimited
                ln, i = _read_varint(data, i)
                chunk, i = data[i:i + ln], i + ln
                if field == 3:
                    out["audio"] = chunk
                elif field == 4:
                    out["uuid"] = chunk.decode("utf-8", "replace")
            elif wt == 1:                      # 64-bit
                i += 8
            elif wt == 5:                      # 32-bit
                i += 4
            else:
                return None                    # unknown wire type
    except (IndexError, ValueError):
        return None
    return out


# ---- the outbound half of the bus framing: stdlib protobuf writers ----
# Mirrors of `_read_varint` above. Fields are written in field-number order with the
# wire type protoc uses, so the bytes are the ones the committed pb2 files produce.

def _write_varint(n: int) -> bytes:
    if n < 0:
        # `n >>= 7` never reaches 0 from below (-1 >> 7 == -1): refuse, do not spin.
        raise ValueError(f"a protobuf varint is unsigned here, got {n}")
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _varint_field(field: int, value: int) -> bytes:
    return _write_varint(field << 3) + _write_varint(int(value))        # wire type 0


def _bytes_field(field: int, data) -> bytes:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return _write_varint((field << 3) | 2) + _write_varint(len(data)) + data


def _float_field(field: int, value: float) -> bytes:
    return _write_varint((field << 3) | 5) + struct.pack("<f", float(value))


def now_ms() -> int:
    """Milliseconds since the epoch: the `timestamp` the robot's protos carry."""
    return time.time_ns() // 1_000_000


def zmq_frame(full_name: str, body: bytes) -> bytes:
    """One `commands/zmq` payload: `b'<full_name>:' + protobuf_bytes`."""
    return full_name.encode("utf-8") + b":" + body


def encode_proto_subscribe(protos: Iterable[str], *,
                           timestamp_ms: Optional[int] = None) -> bytes:
    """The `commands/zmq` frame that asks a robot to stream the named protos to the
    cloud; for the microphone, `[ZMQ_STT_REQUEST]`. Log.proto: timestamp=1 (uint64),
    protos=2 (repeated string)."""
    body = _varint_field(1, now_ms() if timestamp_ms is None else timestamp_ms)
    for name in protos:
        body += _bytes_field(2, name)
    return zmq_frame(PROTO_SUBSCRIBE, body)


#: `error_code` of a FINAL whose transcription failed. The recovered zmqSTT.proto defines
#: the field (uint32 `error_code = 8`, beside `error_message = 9`) but no enum for its
#: values; this is the value the field-proven community server sends with the exception
#: text (OpenMoxie site/hive/mqtt/zmq_stt_handler.py:70-73).
STT_ERROR_CODE = 66


def encode_zmq_stt_response(uuid: str, speech: str, *, final: bool = True,
                            confidence: float = 1.0,
                            timestamp_ms: Optional[int] = None,
                            error_code: int = 0, error_message: str = "") -> bytes:
    """The `commands/zmq` frame carrying one transcript back to the robot. zmqSTT.proto:
    timestamp=1 (uint64), type=2 (PARTIAL=0 / FINAL=1), speech=3 (string), confidence=4
    (float), uuid=7 (string), and on a failed transcription error_code=8 (uint32) and
    error_message=9 (string), written only when set (proto3: an unset field is absent).
    An empty transcript is still a FINAL with `speech == ""`: the robot's turn ends on
    FINAL, not on text; so is a failure, with the error fields filled in."""
    body = (_varint_field(1, now_ms() if timestamp_ms is None else timestamp_ms)
            + _varint_field(2, 1 if final else 0)
            + _bytes_field(3, speech or "")
            + _float_field(4, confidence)
            + _bytes_field(7, uuid or ""))
    if error_code:
        body += _varint_field(8, error_code)
    if error_message:
        body += _bytes_field(9, error_message)
    return zmq_frame(ZMQ_STT_RESPONSE, body)
