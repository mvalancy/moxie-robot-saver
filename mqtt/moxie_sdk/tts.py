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


def strip_markup(markup: str) -> str:
    """The spoken text out of a behavior-markup line (drop <mark .../> + tidy space)."""
    if not markup:
        return ""
    text = _MARK_RE.sub("", markup)
    text = _TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


class Synthesizer:
    """Any text-to-speech engine. Override `synthesize`."""
    name = "synthesizer"
    sample_rate = 24000
    channels = 1

    def synthesize(self, text: str, voice: Optional[str] = None) -> bytes:
        raise NotImplementedError

    @classmethod
    def available(cls) -> bool:
        return True


class OpenAIVoiceSynthesizer(Synthesizer):
    """Server voice via an OpenAI-compatible audio endpoint (`/audio/speech`).
    Lazily imports openai (no hard SDK dep); returns raw PCM by default."""
    name = "openai-voice"

    def __init__(self, base_url: str, api_key: str, voice: str = "alloy",
                 model: str = "tts-1", response_format: str = "pcm",
                 sample_rate: int = 24000):
        from openai import OpenAI          # lazy
        self._client = OpenAI(base_url=base_url, api_key=api_key or "sk-local")
        self._voice, self._model, self._fmt = voice, model, response_format
        self.sample_rate = sample_rate

    def synthesize(self, text: str, voice: Optional[str] = None) -> bytes:
        resp = self._client.audio.speech.create(
            model=self._model, voice=voice or self._voice, input=text,
            response_format=self._fmt)
        return resp.content


def make_voice_synthesizer(base_url: str, api_key: str, voice: str = "alloy",
                           **kw) -> Optional[Synthesizer]:
    """An OpenAIVoiceSynthesizer if a voice endpoint is configured, else None."""
    if not base_url:
        return None
    return OpenAIVoiceSynthesizer(base_url, api_key, voice=voice, **kw)


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


def synthesize_cloud_tts(synth: Synthesizer, markup: str, *, event_id: str = "",
                         voice: Optional[str] = None) -> dict:
    """CloudTTSRequest(markup) → CloudTTSResponse: strip markup → synthesize → wrap."""
    text = strip_markup(markup)
    audio = synth.synthesize(text, voice=voice) if text else b""
    return build_cloud_tts_response(audio, event_id=event_id,
                                    channels=synth.channels,
                                    sample_rate=synth.sample_rate)
