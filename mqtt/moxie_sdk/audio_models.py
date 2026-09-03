"""
Which of a gateway's models are EARS and which are a VOICE — a pure name classifier.

`GET /v1/models` on our LiteLLM gateway returns one flat list: chat models, TTS models
and STT models side by side, with nothing in the payload that says which is which
(LiteLLM's `model_info.mode` is server-side config, not part of the public listing). A
parent console that wants to offer "pick Moxie's voice" / "pick her ears" therefore has
to read the *names*, and the names are the only contract we actually have.

So the rules here are pinned to the model ids the gateway really served on 2026-09-02:

    voice : piper-amy · piper-ryan · graphling-tts-narrator · graphling-tts-character ·
            tts-piper-amy · tts-piper-ryan
    ears  : stt-whisper · graphling-stt · stt-whisper-base
    brain : graphling-small/medium/large, qwen2.5-7b, …  (neither — ignored)

Deliberately pure and offline: no HTTP, no client, no key. Feed it whatever
`client.models.list()` gave you (or a hand-typed list) and it answers. Discovery wiring
is a later slice's job; this is the half that can be tested without spending a request.
"""
from __future__ import annotations
from typing import Dict, List, Optional, Sequence

#: The voice Moxie ships with, when the gateway offers it.
DEFAULT_TTS_MODEL = "piper-amy"
#: The ears we measured fastest (3.4 s for a 6 s clip vs 4.4 s for `graphling-stt`).
DEFAULT_STT_MODEL = "stt-whisper"


def is_tts_model(model_id: str) -> bool:
    """A voice: `piper-…`, `tts-…`, or anything with a `-tts-` segment
    (`graphling-tts-narrator`)."""
    mid = (model_id or "").strip().lower()
    return bool(mid) and (mid.startswith("piper-") or mid.startswith("tts-")
                          or "-tts-" in mid)


def is_stt_model(model_id: str) -> bool:
    """Ears: `stt-…`, anything ending in / containing a `-stt` segment
    (`graphling-stt`), or any model that names `whisper`."""
    mid = (model_id or "").strip().lower()
    return bool(mid) and (mid.startswith("stt-") or "-stt" in mid or "whisper" in mid)


def classify_audio_models(ids: Sequence[str]) -> Dict[str, List[str]]:
    """`{"tts": [...], "stt": [...]}` — audio models only, **input order preserved**.

    Stable order matters: a console picker renders this list, and a picker whose entries
    shuffle between page loads is a bug report. Chat models are dropped, and the TTS rule
    is applied first so an id that somehow matched both can never appear twice.
    """
    tts: List[str] = []
    stt: List[str] = []
    for raw in ids or ():
        mid = (raw or "").strip()
        if not mid:
            continue
        if is_tts_model(mid):
            tts.append(mid)
        elif is_stt_model(mid):
            stt.append(mid)
    return {"tts": tts, "stt": stt}


def default_tts_model(ids: Sequence[str]) -> Optional[str]:
    """`piper-amy` when the gateway serves it (Moxie's own voice), else the first voice
    it does serve, else None — nothing here can speak."""
    voices = classify_audio_models(ids)["tts"]
    if DEFAULT_TTS_MODEL in voices:
        return DEFAULT_TTS_MODEL
    return voices[0] if voices else None


def default_stt_model(ids: Sequence[str]) -> Optional[str]:
    """`stt-whisper` when the gateway serves it, else the first ears it does serve, else
    None — this gateway cannot hear, so a deployment needs local whisper."""
    ears = classify_audio_models(ids)["stt"]
    if DEFAULT_STT_MODEL in ears:
        return DEFAULT_STT_MODEL
    return ears[0] if ears else None
