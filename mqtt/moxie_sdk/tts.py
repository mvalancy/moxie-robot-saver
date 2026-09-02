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
from typing import Optional

_MARK_RE = re.compile(r"<mark\b[^>]*/?>", re.I)     # <mark name="cmd:..."/> behavior tags
_TAG_RE = re.compile(r"<[^>]+>")                    # any residual angle-bracket tag

# Emoji / pictographs. An LLM writes "Sure! 😀" and a TTS engine reads the character's
# Unicode NAME aloud — Piper says "grinning face" mid-sentence (observed in the live
# talk-loop run, PR #12). They carry no speech, so they come off before synthesis;
# ordinary punctuation (!?,.'"-… and friends) is deliberately left alone.
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
    """A voice endpoint answered with something that is not audio.

    Distinct from a transport failure (429/5xx/connection — those are retried by
    `call_with_backoff`) because it is never worth retrying: an unknown model name, a
    revoked key or a proxy that decided to explain itself in JSON will say the same
    thing next time. `FallbackSynthesizer` catches it and speaks with the standby voice.
    """


def voice_for_model(model: str) -> str:
    """The `voice` field to send for a model name — `piper-amy` → `amy`.

    The LiteLLM gateway **requires** `voice` (omitting it is an HTTP 500) but **ignores**
    its value: the model name selects the Piper voice (docs/guides/litellm-tts-setup.md,
    "Live since 2026-09-02"). So the field only has to be present and sane. A model whose
    suffix is not a word — `tts-1` → `1` — falls back to OpenAI's own default voice,
    which is what an OpenAI-shaped endpoint would want anyway.
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

    **Sniff the bytes, never the Content-Type.** Our gateway labels a perfectly good
    Piper WAV `audio/mpeg` (a LiteLLM quirk, observed live 2026-09-02), so a client that
    branched on the header would ship an MP3 decoder at a RIFF file. A RIFF/WAVE payload
    is unwrapped with the stdlib `wave` module and the header's **own** rate/channels come
    back with it — that is how a `CloudTTSResponse` carries the TRUE rate even when the
    voice (and with it the rate) changes under us. Anything else is assumed to be the raw
    PCM we asked for, at the configured rate.

    An error body (a proxy answering 200-with-JSON, or an unknown-model 400 surfaced as
    bytes) raises `VoiceServerError` rather than being handed to a child as noise.
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
    """Server voice via an OpenAI-compatible audio endpoint (`/audio/speech`).

    Lazily imports openai (no hard SDK dep). A busy voice server backs off + paces
    exactly like the LLM gateway (shared chat.call_with_backoff + Pacer) — 429/5xx are
    retried, not failed.

    Live against our LiteLLM gateway since 2026-09-02 (`piper-amy` / `piper-ryan`):

    * `response_format="wav"` (the default) is unwrapped here, so `sample_rate` /
      `channels` come from the **file's own header** — swap `piper-amy` for a 16 kHz
      voice and the CloudTTSResponse follows without a config change.
    * `response_format="pcm"` is passed straight through at the configured
      `sample_rate` (nothing in the payload can tell us otherwise).
    * `voice` is always sent — the gateway 500s without it — defaulting to the model's
      suffix (`piper-amy` → `amy`) when unset. Its value is ignored there; the model is
      the voice.
    """
    name = "openai-voice"

    def __init__(self, base_url: str, api_key: str, voice: Optional[str] = None,
                 model: str = "tts-1", response_format: str = "wav",
                 sample_rate: int = 22050, *, client=None, max_retries: int = 4,
                 channels: int = 1, pacer=None, sleep=None):
        if client is None:
            from openai import OpenAI      # lazy
            client = OpenAI(base_url=base_url, api_key=api_key or "sk-local",
                            max_retries=0)
        from .chat import Pacer
        self._client = client
        self._model, self._fmt = model, response_format
        self._voice = (voice or "").strip() or voice_for_model(model)
        # The CONFIGURED shape — what a raw-PCM reply is assumed to be. `self.sample_rate`
        # is the LAST reply's true rate (a WAV header overrides it); keep them apart so a
        # wav-derived rate can never leak into a later pcm call.
        self._sample_rate, self._channels = int(sample_rate), int(channels)
        self.sample_rate, self.channels = int(sample_rate), int(channels)
        # Injectable like make_openai_chat's — the Pacer binds `time.sleep` at
        # construction, so a test that wants an instant backoff needs its own.
        # Injectable like make_openai_chat's — Pacer and call_with_backoff both bind
        # `time.sleep` at definition time, so a test that wants an instant backoff has to
        # hand its own in rather than patch the module.
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
                                pacer=self._pacer, **kw)
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
    """Local, offline server voice via Piper (https://github.com/rhasspy/piper) — our
    default/primary TTS (Amy). No network, no gateway TTS model, no voice creds needed:
    it synthesizes on the box that runs the supervisor, so the SIM can speak even before
    the gateway registers a TTS model (see docs/guides/litellm-tts-setup.md).

    Piper is imported lazily (no hard dep); `available()` is False when it isn't
    installed. Output is raw 16-bit mono PCM at the voice's own sample rate (Amy-medium
    is 22050 Hz), matching the CloudTTSResponse AudioBuffer convention. Tests inject
    `voice_fn` to exercise the whole path without Piper (like the OpenAI backend's
    `client=`)."""
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
                # piper-tts >= 1.3 renamed the WAV writer; its `synthesize` became a
                # chunk generator, so calling it with a wave writer raises
                # "# channels not specified" (seen with piper-tts 1.3 in the compose
                # `voice` profile). Prefer the explicit WAV entry point when present.
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
    """A built-in, zero-dependency placeholder 'voice': a deterministic 16-bit PCM tone
    shaped to the text length (short fade in/out, no clicks). NOT speech — it lets the
    SIM's audio path work out of the box with no model, network, or extra deps (demos,
    CI, the default before Piper/gateway is configured). Real speech is `PiperSynthesizer`
    (offline) or `OpenAIVoiceSynthesizer` (gateway). Selected with MOXIE_TTS=tone."""
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

    The gateway voice is a network call to someone else's box. An unknown model name, a
    revoked key, a proxy that answers with JSON, an outage past the SDK's backoff: any of
    those used to surface as an exception on the turn's synthesis path, i.e. as NO audio
    at all in the middle of a conversation. This wrapper turns that into a downgrade —
    the standby (`PiperSynthesizer` when a Piper model is configured, else the built-in
    `ToneSynthesizer`) speaks the rest of the run.

    Failure is reported ONCE, on the first failure, and then latched: a dead endpoint
    must not cost every later turn its network timeout, and a parent reading the log must
    not have to scroll past one line per sentence. `failed` and `name` say which voice is
    actually talking, so `/status` and the tests can tell.
    """
    name = "fallback"

    def __init__(self, primary: Synthesizer, standby: Synthesizer, *, log=None):
        self._primary, self._standby = primary, standby
        self._log = log if log is not None else _warn
        self.failed = False
        self.sample_rate, self.channels = primary.sample_rate, primary.channels

    @property
    def voice_name(self) -> str:
        """Which backend is speaking right now."""
        return (self._standby if self.failed else self._primary).name

    def describe(self) -> str:
        if self.failed:
            return f"{self._standby.name} (standby — {self._primary.name} failed)"
        return f"{self._primary.name} (standby: {self._standby.name})"

    def _adopt(self, engine: Synthesizer) -> None:
        self.sample_rate, self.channels = engine.sample_rate, engine.channels

    def synthesize(self, text: str, voice: Optional[str] = None) -> bytes:
        if not self.failed:
            try:
                audio = self._primary.synthesize(text, voice=voice)
                self._adopt(self._primary)
                return audio
            except Exception as exc:                # noqa: BLE001 — any failure downgrades
                self.failed = True
                self._log(f"[voice] {self._primary.name} failed "
                          f"({type(exc).__name__}: {exc}); speaking with "
                          f"{self._standby.name} for the rest of this run")
        audio = self._standby.synthesize(text, voice=voice)
        self._adopt(self._standby)
        return audio


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
    """SIM-side counterpart to build_cloud_tts_response: a CloudTTSResponse (dict or JSON
    string) → `{audio: bytes, sample_rate, channels, marks, event_id, chunk_num}`. This
    is what a client (the SIM's audio playback, a robot) needs to actually speak — it
    base64-decodes the AudioBuffer back to raw PCM. Tolerant of missing/partial fields."""
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

    `chunk_num` rides through to the response so a multi-chunk turn (a filler chunk 0
    followed by the real answer as chunk 1 — see the runtime's brain-latency budget)
    plays back in order: a client queues the chunks of one `event_id` by `chunk_num`
    (docs/architecture/sim-as-a-client.md:77)."""
    text = strip_markup(markup)
    audio = synth.synthesize(text, voice=voice) if text else b""
    return build_cloud_tts_response(audio, event_id=event_id,
                                    channels=synth.channels,
                                    sample_rate=synth.sample_rate,
                                    chunk_num=chunk_num)
