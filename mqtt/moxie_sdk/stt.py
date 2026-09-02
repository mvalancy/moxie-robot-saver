"""
STT seam (AI-seam §1) — turn the robot's streamed mic audio into a recognized
utterance. Transport-free + pluggable: a `Transcriber` (Whisper, or any engine, or a
Deepgram-shaped proxy) behind a small interface, and an `SttSession` that accumulates
`zmqSTT` audio frames by VAD state and emits the utterance on END_OF_SPEECH.

Two engines ship, and **both are first class** — which one you want is a property of the
deployment, not a quality ranking:

* `WhisperTranscriber` — local faster-whisper. No network, no key, nothing leaves the
  house. What a home appliance (or an offline demo) should run; `MOXIE_STT=whisper`
  selects it even when a gateway URL is configured.
* `OpenAITranscriber` — an OpenAI-compatible `/audio/transcriptions` endpoint (our
  LiteLLM gateway, live since 2026-09-02). What a *hosted* deployment needs, where there
  is no box to put a 140 MB model on: same host, same key and same rate limits as the
  brain and the voice. `MOXIE_STT=gateway`; `auto` picks it when one is configured.

`FallbackTranscriber` puts one behind the other, so a gateway outage costs a downgrade
(local ears, or an honest "" from `NullTranscriber`) rather than a raised exception in
the middle of a child's sentence — the same latch-and-report-once contract the voice
path uses (`tts.py::FallbackSynthesizer`).

Wire shapes verbatim from embodied/perception/audio/zmqSTT.proto:
  zmqSTTRequest { VADState vad; bytes audio_content; string uuid }   VAD: UNKNOWN=0,
      START_OF_SPEECH=1, SPEECH=2, END_OF_SPEECH=3
  zmqSTTResponse { ResponseType type (PARTIAL=0/FINAL=1); string speech; float
      confidence; string uuid; ... }
"""
from __future__ import annotations
import time
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
    """Local STT via faster-whisper (CPU/GPU). Imported lazily so the SDK has no hard
    dependency; `available()` is False when it (or numpy) isn't installed.

    A first-class engine, not a consolation prize: it is the right answer for a home
    appliance (no network egress, no key, no per-utterance latency to someone else's
    box) and `MOXIE_STT=whisper` selects it even when a gateway is configured."""
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
    """A transcription endpoint answered with something that is not a transcript.

    Distinct from a transport failure (429/5xx/connection — retried by
    `call_with_backoff`) because it is never worth retrying: an unknown model name, a
    revoked key or a proxy that decided to answer with a bare error object will say the
    same thing next time. `FallbackTranscriber` catches it and listens with the standby.
    """


def wav_bytes(pcm: bytes, sample_rate: int = 16000, *, channels: int = 1,
              sample_width: int = 2) -> bytes:
    """16-bit mono PCM → a RIFF/WAVE **file** in memory (stdlib `wave`, no deps).

    `/audio/transcriptions` is a multipart upload of a *file*, and the robot's mic gives
    us headerless frames — so the container has to be made here. The rate written is the
    one handed in (the perception bus streams 16 kHz; a WAV from the TTS side may be
    22050), which is the whole point: a header that lied about the rate would pitch-shift
    the audio and wreck the transcript.
    """
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
    """Cloud ears via an OpenAI-compatible `/audio/transcriptions` endpoint.

    Live on our LiteLLM gateway since 2026-09-02 — same host, same key and same rate
    limits as the brain and the voice, which is the whole reason it exists: a *hosted*
    deployment (the SIM on Cloudflare, a VPS, a container with no model volume) has
    nowhere to put faster-whisper's weights, and this makes hearing one env line instead
    of a 140 MB download. On a box that can hold the model, local whisper is still a
    first-class choice — see `MOXIE_STT` in `mqtt/config.py`.

    Verified shapes (2026-09-02): models `stt-whisper` (default), `graphling-stt`,
    `stt-whisper-base`; a 22050 Hz mono 16-bit WAV came back word for word; an unknown
    model is a 400. The reply is `{"text": "...", "usage": null}`.

    Lazily imports openai (no hard SDK dep) and takes the same seams as
    `OpenAIVoiceSynthesizer`: `client=` for a fake, `pacer=`/`sleep=` for an instant
    backoff, so every test in `sim/tests/test_stt_gateway.py` runs with **no openai
    installed at all**. 429/5xx are retried and paced by the shared
    `chat.call_with_backoff` + `Pacer`, exactly like the LLM and the voice.
    """
    name = "openai-stt"

    #: Shortest utterance worth a network round trip. A robot's VAD closes on breaths and
    #: door slams; anything under this is silence to any ASR, and the gateway would charge
    #: a request (and ~3 s) to tell us so.
    MIN_MS = 120

    def __init__(self, base_url: str, api_key: str, model: str = "stt-whisper", *,
                 client=None, max_retries: int = 4, pacer=None, sleep=time.sleep,
                 min_ms: int = MIN_MS, language: Optional[str] = None):
        if client is None:
            from openai import OpenAI      # lazy — the module imports without openai
            client = OpenAI(base_url=base_url, api_key=api_key or "sk-local",
                            max_retries=0)
        from .chat import Pacer
        self._client = client
        #: Public on purpose — a console model picker (and `describe()`) reads it.
        self.model = model
        self.base_url = base_url
        self._language = language
        # Injectable like make_openai_chat's: Pacer and call_with_backoff both bind
        # `time.sleep` at definition time, so a test that wants an instant backoff has to
        # hand its own in rather than patch the module.
        self._pacer = pacer if pacer is not None else Pacer()
        self._sleep = sleep
        self._max_retries = max_retries
        self._min_ms = int(min_ms)

    def describe(self) -> str:
        return f"{self.name} ({self.model})"

    @classmethod
    def available(cls, base_url: str = "") -> bool:
        """True when this engine could actually run: the openai SDK is importable **and**
        an endpoint is configured. Mirrors `WhisperTranscriber.available()` (which asks
        the same question of faster-whisper + numpy) — an unconfigured cloud engine is as
        unavailable as an uninstalled local one."""
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
            # A FRESH stream per attempt: a retry that re-sent a consumed BytesIO would
            # upload zero bytes and get an empty transcript back, which is worse than the
            # 429 it was retrying.
            kw = {"language": self._language} if self._language else {}
            return self._client.audio.transcriptions.create(
                model=self.model,
                file=("utterance.wav", io.BytesIO(wav), "audio/wav"),
                response_format="json", **kw)

        resp = call_with_backoff(_once, max_retries=self._max_retries,
                                 pacer=self._pacer, sleep=self._sleep)
        return transcript_text(resp)


class NullTranscriber(Transcriber):
    """The bottom rung: hears nothing and says so, by returning "".

    It exists so `FallbackTranscriber` always has something to fall back *to* on a box
    with no local whisper installed. An empty transcript is what the runtime already
    does with an empty utterance, so a gateway outage degrades to "Moxie didn't catch
    that" instead of an exception mid-turn."""
    name = "no-ears"

    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        return ""


class FallbackTranscriber(Transcriber):
    """A primary set of ears with a standby behind them — the STT twin of
    `tts.py::FallbackSynthesizer`, and it exists for the same reason.

    The gateway is a network call to someone else's box. An unknown model name, a
    revoked key, an outage past the SDK's backoff: any of those surfaces as an exception
    on the turn's transcription path, i.e. as a *crash* where a child expected to be
    heard. This wrapper turns that into a downgrade — the standby (local
    `WhisperTranscriber` when it is installed, else `NullTranscriber`) does the hearing
    for the rest of the run.

    Failure is reported ONCE, on the first failure, and then latched: a dead endpoint
    must not cost every later utterance its network timeout, and a parent reading the log
    must not scroll past one line per sentence. `failed` and `describe()` say which
    engine is actually listening, so `/status` and the tests can tell.
    """
    name = "fallback"

    def __init__(self, primary: Transcriber, standby: Transcriber, *, log=None):
        self._primary, self._standby = primary, standby
        self._log = log if log is not None else _warn
        self.failed = False

    @property
    def engine(self) -> Transcriber:
        """The engine that is doing the hearing right now."""
        return self._standby if self.failed else self._primary

    @property
    def engine_name(self) -> str:
        return self.engine.name

    def describe(self) -> str:
        if self.failed:
            return (f"{self._standby.describe()} (standby — "
                    f"{self._primary.name} failed)")
        return f"{self._primary.describe()} (standby: {self._standby.describe()})"

    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        if not self.failed:
            try:
                return self._primary.transcribe(pcm, sample_rate)
            except Exception as exc:            # noqa: BLE001 — any failure downgrades
                self.failed = True
                self._log(f"[stt] {self._primary.name} failed "
                          f"({type(exc).__name__}: {exc}); hearing with "
                          f"{self._standby.name} for the rest of this run")
        return self._standby.transcribe(pcm, sample_rate)


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


def build_stt_response(uuid: str, speech: str, *, final: bool = True,
                       confidence: float = 1.0) -> dict:
    """A zmqSTTResponse (JSON) a revival server publishes back after transcription."""
    return {"type": "FINAL" if final else "PARTIAL", "speech": speech,
            "confidence": confidence, "uuid": uuid}
