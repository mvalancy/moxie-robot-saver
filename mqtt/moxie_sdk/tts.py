"""
TTS seam (AI-seam §3) — server-side voice so the SIM (and optionally a robot) speaks.
Transport-free + pluggable: a `Synthesizer` behind a small interface, `strip_markup`
to get the spoken text out of behavior markup, and the CloudTTSResponse encoder.

On a real robot TTS is on-device (the server sends text+markup and Moxie synthesizes);
server-side TTS is what the SIM needs — see docs/architecture/sim-as-a-client.md.

Wire shapes from embodied/unity/CloudTTS.proto:
  CloudTTSRequest  { string markup; string event_id; int32 chunk_num; ... }
  AudioBuffer      { bytes buffer; int32 channels; int32 sample_rate }
  TTSMark          { uint32 time; uint32 start; uint32 end; string type; string value }
  CloudTTSResponse { RequestSourceType request_source; AudioBuffer audio;
                     repeated TTSMark marks; string event_id; int32 chunk_num; ... }
"""
from __future__ import annotations
import base64
import re
import threading
import time
from typing import Optional

_MARK_RE = re.compile(r"<mark\b[^>]*/?>", re.I)     # <mark name="cmd:..."/> behavior tags
_TAG_RE = re.compile(r"<[^>]+>")                    # any residual angle-bracket tag

# Emoji / pictographs: a TTS engine reads their Unicode NAME aloud ("grinning face"), so
# they come off before synthesis. Ordinary punctuation is left alone.
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"      # emoticons, pictographs, transport, flags, symbols ext-A
    "☀-➿"              # miscellaneous symbols + dingbats (sun, sparkles, check)
    "⬀-⯿"              # miscellaneous symbols and arrows (star, arrows, blocks)
    "\ufe00-\ufe0f"            # variation selectors (the invisible one after a heart)
    "\u200d\u20e3"             # zero-width joiner + combining enclosing keycap
    "©®™ℹ"   # (c) (r) (tm) information source
    "⤴⤵〰〽㊗㊙"
    "]"
)


def strip_markup(markup: str) -> str:
    """The spoken text out of a behavior-markup line (drop <mark .../> and emoji, tidy
    space). What comes back is what a TTS engine should actually say."""
    if not markup:
        return ""
    text = _MARK_RE.sub("", markup)
    text = _TAG_RE.sub("", text)
    text = _EMOJI_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


class Synthesizer:
    """Any text-to-speech engine. Override `synthesize`."""
    name = "synthesizer"
    sample_rate = 24000
    channels = 1

    def synthesize(self, text: str, voice: Optional[str] = None) -> bytes:
        raise NotImplementedError

    def describe(self) -> str:
        """One line for a startup log — which voice is this, really."""
        return self.name

    @classmethod
    def available(cls) -> bool:
        return True


class VoiceServerError(RuntimeError):
    """A voice endpoint answered with something that is not audio. Not retried (unlike
    429/5xx); `FallbackSynthesizer` catches it and speaks with the standby voice."""


def voice_for_model(model: str) -> str:
    """The `voice` field to send for a model name — `piper-amy` → `amy`, else `alloy`.

    LiteLLM requires `voice` (500 without it) but the model selects the Piper voice
    (docs/guides/gateway-voice-and-ears.md), so it only has to be present and sane.
    """
    tail = (model or "").rsplit("-", 1)[-1].strip()
    return tail if tail.isalpha() else "alloy"


def _json_error(raw: bytes):
    """A one-line summary if `raw` is a JSON error body, else None."""
    if raw[:1] not in (b"{", b"["):
        return None
    import json as _json
    try:
        body = _json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return None
    detail = body
    if isinstance(body, dict):
        detail = body.get("error", body)
        if isinstance(detail, dict):
            detail = detail.get("message", detail)
    return str(detail)[:300]


def pcm_from_audio(raw: bytes, *, sample_rate: int, channels: int = 1):
    """`(pcm16, sample_rate, channels)` from whatever an `/audio/speech` call returned.

    Sniffs the bytes, never the Content-Type (LiteLLM labels WAV `audio/mpeg`). A WAV is
    unwrapped and its header's own rate/channels returned; anything else is taken as raw
    PCM at the configured rate. A JSON error body raises `VoiceServerError`.
    """
    if not raw:
        raise VoiceServerError("the voice server returned an empty body (no audio)")
    detail = _json_error(raw)
    if detail is not None:
        raise VoiceServerError(f"the voice server returned JSON, not audio: {detail}")
    if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        import io
        import wave
        try:
            with wave.open(io.BytesIO(raw), "rb") as w:
                width, rate, ch = w.getsampwidth(), w.getframerate(), w.getnchannels()
                pcm = w.readframes(w.getnframes())
        except (wave.Error, EOFError) as exc:
            raise VoiceServerError(f"unreadable WAV from the voice server: {exc}") from exc
        if width != 2:
            raise VoiceServerError(
                f"the voice server sent {width * 8}-bit WAV; CloudTTSResponse.AudioBuffer "
                f"is 16-bit PCM")
        return pcm, int(rate), int(ch)
    return raw, int(sample_rate), int(channels)


class OpenAIVoiceSynthesizer(Synthesizer):
    """Server voice via an OpenAI-compatible `/audio/speech` endpoint (openai imported
    lazily). 429/5xx back off like the LLM gateway (`chat.call_with_backoff` + Pacer).

    `wav` replies carry their own rate/channels; `pcm` uses the configured rate. `voice`
    is always sent (see `voice_for_model`).
    """
    name = "openai-voice"

    #: Seconds one speech request may take (config: MOXIE_TTS_TIMEOUT_S); the SDK's own
    #: default was 600 s, and the standby voice is waiting behind it. Chosen, not measured.
    TIMEOUT_S = 15.0

    def __init__(self, base_url: str, api_key: str, voice: Optional[str] = None,
                 model: str = "tts-1", response_format: str = "wav",
                 sample_rate: int = 22050, *, client=None, max_retries: int = 4,
                 channels: int = 1, pacer=None, sleep=None,
                 timeout_s: Optional[float] = None):
        from .chat import Pacer, timeout_seconds
        # None = `TIMEOUT_S`; 0 or less is refused here, never read as "no bound".
        self._timeout_s = timeout_seconds(timeout_s, default=self.TIMEOUT_S)
        if client is None:
            from openai import OpenAI      # lazy
            from .chat import client_timeout
            client = OpenAI(base_url=base_url, api_key=api_key or "sk-local",
                            max_retries=0, timeout=client_timeout(self._timeout_s))
        self._client = client
        self._model, self._fmt = model, response_format
        self._voice = (voice or "").strip() or voice_for_model(model)
        # Configured shape (for raw PCM) vs the last reply's true shape, kept apart so a
        # WAV-derived rate never leaks into a later pcm call.
        self._sample_rate, self._channels = int(sample_rate), int(channels)
        self.sample_rate, self.channels = int(sample_rate), int(channels)
        # Injectable (Pacer/backoff bind `time.sleep` early) so tests can back off instantly.
        self._pacer = pacer if pacer is not None else Pacer()
        self._sleep = sleep
        self._max_retries = max_retries

    def synthesize(self, text: str, voice: Optional[str] = None) -> bytes:
        from .chat import call_with_backoff

        def _once():
            resp = self._client.audio.speech.create(
                model=self._model, voice=voice or self._voice, input=text,
                response_format=self._fmt)
            return resp.content
        kw = {} if self._sleep is None else {"sleep": self._sleep}
        raw = call_with_backoff(_once, max_retries=self._max_retries,
                                pacer=self._pacer, deadline_s=self._timeout_s, **kw)
        pcm, rate, channels = pcm_from_audio(raw, sample_rate=self._sample_rate,
                                             channels=self._channels)
        self.sample_rate, self.channels = rate, channels
        return pcm


def make_voice_synthesizer(base_url: str, api_key: str, voice: Optional[str] = None,
                           **kw) -> Optional[Synthesizer]:
    """An OpenAIVoiceSynthesizer if a voice endpoint is configured, else None."""
    if not base_url:
        return None
    return OpenAIVoiceSynthesizer(base_url, api_key, voice=voice, **kw)


class PiperSynthesizer(Synthesizer):
    """Local, offline server voice via Piper (https://github.com/rhasspy/piper).

    Imported lazily; `available()` is False without it. Output is 16-bit mono PCM at the
    voice's own rate. Tests inject `voice_fn` to run without Piper."""
    name = "piper"
    channels = 1

    def __init__(self, model_path: str = "", config_path: Optional[str] = None,
                 sample_rate: int = 22050, *, voice_fn=None):
        self._model_path = model_path
        if voice_fn is not None:                 # test / custom injection
            self._voice_fn = voice_fn
            self.sample_rate = sample_rate
            return
        from piper import PiperVoice              # lazy — real backend
        voice = PiperVoice.load(model_path, config_path=config_path)
        cfg = getattr(voice, "config", None)
        self.sample_rate = int(getattr(cfg, "sample_rate", sample_rate) or sample_rate)
        # piper yields raw PCM chunks; join to one buffer (version-tolerant)
        def _fn(text: str) -> bytes:
            if hasattr(voice, "synthesize_stream_raw"):     # piper-tts <= 1.2
                return b"".join(voice.synthesize_stream_raw(text))
            import io, wave                        # fallback: capture WAV, return PCM
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                # piper-tts >= 1.3: `synthesize` yields chunks; use `synthesize_wav`.
                if hasattr(voice, "synthesize_wav"):
                    voice.synthesize_wav(text, w)
                else:
                    voice.synthesize(text, w)
            buf.seek(0)
            with wave.open(buf, "rb") as r:
                return r.readframes(r.getnframes())
        self._voice_fn = _fn

    def synthesize(self, text: str, voice: Optional[str] = None) -> bytes:
        return self._voice_fn(text)

    @classmethod
    def available(cls) -> bool:
        try:
            import piper  # noqa: F401
            return True
        except Exception:
            return False


class ToneSynthesizer(Synthesizer):
    """Zero-dependency placeholder 'voice' (MOXIE_TTS=tone): a deterministic 16-bit PCM tone
    sized to the text, faded at the edges. Not speech — it exercises the audio path in
    demos/CI with no model or network."""
    name = "tone"

    def __init__(self, sample_rate: int = 22050, freq: float = 330.0,
                 ms_per_char: int = 55, min_ms: int = 200, max_ms: int = 4000):
        self.sample_rate = sample_rate
        self._freq, self._ms_per_char = freq, ms_per_char
        self._min_ms, self._max_ms = min_ms, max_ms

    def synthesize(self, text: str, voice: Optional[str] = None) -> bytes:
        import math
        from array import array
        ms = min(self._max_ms, max(self._min_ms, len(text or "") * self._ms_per_char))
        n = int(self.sample_rate * ms / 1000)
        buf = array("h", bytes(2 * n))
        amp, fade = 12000, 200
        w = 2 * math.pi * self._freq / self.sample_rate
        for i in range(n):
            env = min(1.0, i / fade, (n - i) / fade)     # fade edges to avoid clicks
            buf[i] = int(amp * env * math.sin(w * i))
        return buf.tobytes()


def make_piper_synthesizer(model_path: str, config_path: Optional[str] = None,
                           *, voice_fn=None, **kw) -> Optional[Synthesizer]:
    """A PiperSynthesizer when a model is configured and Piper is installed (or a
    `voice_fn` is injected), else None."""
    if voice_fn is not None:
        return PiperSynthesizer(model_path, config_path, voice_fn=voice_fn, **kw)
    if not model_path or not PiperSynthesizer.available():
        return None
    return PiperSynthesizer(model_path, config_path, **kw)


class FallbackSynthesizer(Synthesizer):
    """A primary voice with a standby behind it — so a child never hears silence.

    Any primary failure downgrades to the standby. Reported once and latched, so a dead
    endpoint does not cost every later turn its timeout — but not for the rest of the
    run: after `retry_s` (config: MOXIE_ENGINE_RETRY_S) the next line tries the primary
    again, and an answer clears the latch with one recovery line. `failed` /
    `voice_name` say which voice is talking.
    """
    name = "fallback"

    #: Seconds a latched standby holds before the next line tries the primary again.
    #: 0 = try the primary on every line (no latch). Chosen, not measured.
    RETRY_S = 60.0

    def __init__(self, primary: Synthesizer, standby: Synthesizer, *, log=None,
                 retry_s: Optional[float] = None, clock=time.time):
        self._primary, self._standby = primary, standby
        self._log = log if log is not None else _warn
        self._retry_s = max(0.0, float(self.RETRY_S if retry_s is None else retry_s))
        self._clock = clock                     # wall clock: `describe()` names the time
        # The latch is `failed_at`: the instant it (last) closed, None while healthy;
        # `failed` is the public flag beside it. Both move under `_lock`, and every
        # reader takes ONE snapshot of `failed_at` — `synthesize()` runs on turn workers
        # and filler timers at once, `describe()` on the status thread.
        self._lock = threading.Lock()
        self.failed = False
        self.failed_at: Optional[float] = None
        self.sample_rate, self.channels = primary.sample_rate, primary.channels

    @property
    def voice_name(self) -> str:
        """Which backend is speaking right now."""
        return (self._standby if self.failed else self._primary).name

    def retry_at(self) -> Optional[float]:
        """When the primary is tried again (wall clock), or None while it is healthy."""
        at = self.failed_at
        return None if at is None else at + self._retry_s

    def describe(self) -> str:
        at = self.failed_at
        if at is None:
            return f"{self._primary.name} (standby: {self._standby.name})"
        # Past the window the time has gone by: say what happens instead of when it was.
        when = ("on the next line" if self._clock() >= at + self._retry_s
                else f"at {_hhmm(at + self._retry_s)}")
        return (f"{self._standby.name} (standby since {_hhmm(at)} — "
                f"{self._primary.name} failed; retrying the primary {when})")

    def _adopt(self, engine: Synthesizer) -> None:
        self.sample_rate, self.channels = engine.sample_rate, engine.channels

    def _try_primary(self) -> bool:
        """Whether THIS line goes to the primary: always while healthy; once the window
        has passed, for the one caller that claims the retry — the window moves at
        once, so a line racing it on another thread (a filler timer beside a turn
        worker) stays on the standby instead of spending a second timeout."""
        with self._lock:
            at = self.failed_at
            if at is None:
                return True
            if self._clock() >= at + self._retry_s:
                self.failed_at = self._clock()
                return True
            return False

    def _latch(self, exc: Exception) -> None:
        with self._lock:
            first = self.failed_at is None
            self.failed_at = self._clock()
            self.failed = True
        if first:
            self._log(f"[voice] {self._primary.name} failed ({type(exc).__name__}: "
                      f"{exc}); speaking with {self._standby.name} until it answers "
                      f"again (next try in {self._retry_s:g}s)")
        else:
            self._log(f"[voice] {self._primary.name} still failing "
                      f"({type(exc).__name__}); speaking with {self._standby.name}, "
                      f"next try in {self._retry_s:g}s")

    def _release(self) -> None:
        with self._lock:
            was_latched = self.failed_at is not None
            self.failed, self.failed_at = False, None
        if was_latched:
            self._log(f"[voice] {self._primary.name} is back; speaking with it again")

    def synthesize(self, text: str, voice: Optional[str] = None) -> bytes:
        if self._try_primary():
            try:
                audio = self._primary.synthesize(text, voice=voice)
            except Exception as exc:                # noqa: BLE001 — any failure downgrades
                self._latch(exc)
            else:
                self._release()
                self._adopt(self._primary)
                return audio
        audio = self._standby.synthesize(text, voice=voice)
        self._adopt(self._standby)
        return audio


def _hhmm(t: Optional[float]) -> str:
    """A wall-clock instant as `HH:MM ZONE` (the supervisor's local zone — UTC in the
    container) for a startup/status line."""
    return time.strftime("%H:%M %Z", time.localtime(t or 0))


def _warn(message: str) -> None:
    print(message, flush=True)


def build_cloud_tts_response(audio: bytes, *, event_id: str = "", channels: int = 1,
                             sample_rate: int = 24000, marks: Optional[list] = None,
                             chunk_num: int = 0, request_source: str = "ROBOT_TTS_REQUEST"
                             ) -> dict:
    """Build the CloudTTSResponse JSON (audio.buffer base64-encoded for the wire)."""
    return {
        "request_source": request_source,
        "audio": {"buffer": base64.b64encode(audio or b"").decode(),
                  "channels": channels, "sample_rate": sample_rate},
        "marks": list(marks or []),
        "event_id": event_id,
        "chunk_num": chunk_num,
    }


def decode_cloud_tts_response(resp: dict) -> dict:
    """Client-side inverse of `build_cloud_tts_response`: a CloudTTSResponse (dict or JSON)
    → `{audio, sample_rate, channels, marks, event_id, chunk_num}`. Tolerant of gaps."""
    if isinstance(resp, (str, bytes)):
        import json as _json
        resp = _json.loads(resp)
    audio_obj = resp.get("audio") or {}
    buf = audio_obj.get("buffer") or ""
    try:
        audio = base64.b64decode(buf) if buf else b""
    except Exception:
        audio = b""
    return {
        "audio": audio,
        "sample_rate": int(audio_obj.get("sample_rate", 24000) or 24000),
        "channels": int(audio_obj.get("channels", 1) or 1),
        "marks": list(resp.get("marks") or []),
        "event_id": resp.get("event_id", ""),
        "chunk_num": int(resp.get("chunk_num", 0) or 0),
    }


def synthesize_cloud_tts(synth: Synthesizer, markup: str, *, event_id: str = "",
                         voice: Optional[str] = None, chunk_num: int = 0) -> dict:
    """CloudTTSRequest(markup) → CloudTTSResponse: strip markup → synthesize → wrap.
    `chunk_num` rides through so a client plays a multi-chunk turn in order."""
    text = strip_markup(markup)
    audio = synth.synthesize(text, voice=voice) if text else b""
    return build_cloud_tts_response(audio, event_id=event_id,
                                    channels=synth.channels,
                                    sample_rate=synth.sample_rate,
                                    chunk_num=chunk_num)
