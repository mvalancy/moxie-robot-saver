"""
TTS seam tests (M4) — markup stripping, the CloudTTSResponse encoder, and the
synthesize flow with a fake synthesizer. Pure (no voice server); the OpenAI backend
is exercised only for availability/skip.
"""
import base64
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.tts import (  # noqa: E402
    strip_markup, Synthesizer, build_cloud_tts_response, synthesize_cloud_tts,
    make_voice_synthesizer, decode_cloud_tts_response,
)


class _FakeSynth(Synthesizer):
    name = "fake"
    sample_rate = 16000
    channels = 1

    def __init__(self):
        self.said = None

    def synthesize(self, text, voice=None):
        self.said = text
        return b"PCMDATA"


def test_strip_markup_leaves_spoken_text():
    markup = '<mark name="cmd:playback-mood,data:{+mood+:1}"/>Hi Sam!<mark name="cmd:x"/>'
    assert strip_markup(markup) == "Hi Sam!"
    assert strip_markup("") == ""
    assert strip_markup("just text") == "just text"


def test_strip_markup_drops_emoji_so_piper_never_reads_them_aloud():
    """A TTS engine speaks an emoji's Unicode NAME — Piper said "grinning face"
    mid-sentence in the PR #12 talk-loop run. LLMs sprinkle them, so they come off
    before synthesis. Ordinary punctuation must survive untouched."""
    assert strip_markup("Sure! \U0001F600 Let's play.") == "Sure! Let's play."
    assert strip_markup("I love it \u2764\ufe0f\u2b50") == "I love it"
    assert strip_markup("Hi \U0001F44B\U0001F3FD there") == "Hi there"          # skin-tone modifier
    assert strip_markup("\U0001F469\u200D\U0001F680 astronaut") == "astronaut"  # ZWJ sequence
    assert strip_markup("\U0001F1FA\U0001F1F8 flag") == "flag"                   # regional indicators
    assert strip_markup("\u2705 done \u27A1 next") == "done next"
    assert strip_markup("\U0001F600") == "", "an emoji-only line has nothing to say"
    # …and the words a child's turn is actually made of are left exactly alone.
    kept = "Hello \u2014 it's 3:45, \"ok\"? (yes) 50% #1 & more\u2026 caf\u00e9 na\u00efve"
    assert strip_markup(kept) == kept


def test_strip_markup_drops_emoji_inside_behavior_markup():
    markup = '<mark name="cmd:playback-mood,data:{+mood+:1}"/>Yay \U0001F389 you did it!'
    assert strip_markup(markup) == "Yay you did it!"


def test_cloud_tts_response_shape():
    r = build_cloud_tts_response(b"abc", event_id="e1", channels=1, sample_rate=16000)
    assert r["event_id"] == "e1"
    assert r["audio"]["channels"] == 1 and r["audio"]["sample_rate"] == 16000
    assert base64.b64decode(r["audio"]["buffer"]) == b"abc"     # audio is base64 on the wire
    assert r["marks"] == [] and r["request_source"] == "ROBOT_TTS_REQUEST"


def test_synthesize_cloud_tts_flow():
    synth = _FakeSynth()
    resp = synthesize_cloud_tts(synth, '<mark name="cmd:x"/>Hello there', event_id="e9")
    assert synth.said == "Hello there"                          # markup stripped before TTS
    assert base64.b64decode(resp["audio"]["buffer"]) == b"PCMDATA"
    assert resp["audio"]["sample_rate"] == 16000 and resp["event_id"] == "e9"


def test_empty_markup_yields_no_audio_no_call():
    synth = _FakeSynth()
    resp = synthesize_cloud_tts(synth, '<mark name="cmd:x"/>')
    assert synth.said is None                                   # nothing to speak → no TTS call
    assert base64.b64decode(resp["audio"]["buffer"]) == b""


def test_make_voice_synthesizer_needs_a_base_url():
    assert make_voice_synthesizer("", "key") is None           # not configured → None


def test_voice_synth_backs_off_on_rate_limit():
    """A busy voice server (429) is retried with backoff, not failed — same resilience
    as the LLM gateway."""
    from moxie_sdk.tts import OpenAIVoiceSynthesizer

    class _RateLimit(Exception):
        status_code = 429

    class _Resp:
        content = b"PCMOK"

    class _FakeClient:
        def __init__(self):
            self.calls = 0
            self.audio = self
            self.speech = self

        def create(self, **kw):
            self.calls += 1
            if self.calls < 3:
                raise _RateLimit()
            return _Resp()

    fake = _FakeClient()
    synth = OpenAIVoiceSynthesizer("", "", client=fake, response_format="pcm")
    # patch the backoff sleep so the test is instant
    import moxie_sdk.chat as chat
    orig = chat.time.sleep
    chat.time.sleep = lambda s: None
    try:
        out = synth.synthesize("hello")
    finally:
        chat.time.sleep = orig
    assert out == b"PCMOK" and fake.calls == 3      # retried through 2 rate-limits


# --- local Piper voice (our default/primary, offline) ---

def test_piper_available_is_bool():
    from moxie_sdk.tts import PiperSynthesizer
    # piper isn't installed in CI → available() is a clean False (never raises)
    assert PiperSynthesizer.available() in (True, False)


def test_piper_synth_full_path_with_injected_voice_fn():
    """The whole strip→synthesize→CloudTTSResponse path works with an injected voice_fn,
    so we exercise it hermetically without Piper installed."""
    from moxie_sdk.tts import PiperSynthesizer
    seen = {}

    def fake_voice(text):
        seen["text"] = text
        return b"\x00\x01" * 8                      # 16 bytes of raw PCM
    synth = PiperSynthesizer("en_US-amy-medium.onnx", voice_fn=fake_voice,
                             sample_rate=22050)
    assert synth.name == "piper" and synth.sample_rate == 22050 and synth.channels == 1
    resp = synthesize_cloud_tts(synth, '<mark name="cmd:x"/>Hi Amy', event_id="p1")
    assert seen["text"] == "Hi Amy"                 # markup stripped before synthesis
    assert base64.b64decode(resp["audio"]["buffer"]) == b"\x00\x01" * 8
    assert resp["audio"]["sample_rate"] == 22050 and resp["event_id"] == "p1"


def test_make_piper_synthesizer_selection():
    from moxie_sdk.tts import make_piper_synthesizer, PiperSynthesizer
    # no model path → None (nothing to load)
    assert make_piper_synthesizer("") is None
    # a model path but Piper not installed → None (unless a voice_fn is injected)
    if not PiperSynthesizer.available():
        assert make_piper_synthesizer("some.onnx") is None
    # injected voice_fn always builds (test/custom backend)
    s = make_piper_synthesizer("some.onnx", voice_fn=lambda t: b"x")
    assert isinstance(s, PiperSynthesizer) and s.synthesize("hi") == b"x"


# --- SIM-side playback: decode CloudTTSResponse back to audio ---

def test_decode_cloud_tts_round_trips_build():
    marks = [{"time": 0, "start": 0, "end": 2, "type": "word", "value": "Hi"}]
    wire = build_cloud_tts_response(b"\x10\x20\x30\x40", event_id="e5", channels=1,
                                    sample_rate=22050, marks=marks)
    got = decode_cloud_tts_response(wire)
    assert got["audio"] == b"\x10\x20\x30\x40"          # base64 buffer → raw PCM
    assert got["sample_rate"] == 22050 and got["channels"] == 1
    assert got["event_id"] == "e5" and got["marks"] == marks


def test_decode_cloud_tts_accepts_json_string_and_partial():
    import json
    wire = build_cloud_tts_response(b"abc", event_id="e6")
    got = decode_cloud_tts_response(json.dumps(wire))     # tolerates a JSON string
    assert got["audio"] == b"abc" and got["event_id"] == "e6"
    empty = decode_cloud_tts_response({})                 # missing fields → safe defaults
    assert empty["audio"] == b"" and empty["sample_rate"] == 24000 and empty["marks"] == []


# --- built-in zero-dep tone voice ---

def test_tone_synth_produces_deterministic_pcm():
    from moxie_sdk.tts import ToneSynthesizer
    s = ToneSynthesizer(sample_rate=22050)
    a = s.synthesize("hello there friend")
    assert isinstance(a, bytes) and len(a) > 0 and len(a) % 2 == 0   # 16-bit PCM
    assert s.synthesize("hello there friend") == a                  # deterministic
    assert s.name == "tone" and s.sample_rate == 22050


def test_tone_synth_length_scales_with_text_and_is_bounded():
    from moxie_sdk.tts import ToneSynthesizer
    s = ToneSynthesizer()
    short, long = len(s.synthesize("hi")), len(s.synthesize("hi " * 50))
    assert long > short                                             # longer text → more audio
    huge = len(s.synthesize("word " * 10000))
    assert huge <= 2 * int(s.sample_rate * s._max_ms / 1000)        # capped at max_ms


def test_tone_synth_through_cloud_tts():
    from moxie_sdk.tts import ToneSynthesizer
    resp = synthesize_cloud_tts(ToneSynthesizer(), '<mark name="cmd:x"/>Hi', event_id="t1")
    got = decode_cloud_tts_response(resp)
    assert got["audio"] and got["sample_rate"] == 22050 and got["event_id"] == "t1"


# ============================================================================
# The GATEWAY voice (live on our LiteLLM proxy since 2026-09-02)
#
# `piper-amy` / `piper-ryan` are registered there and `POST /v1/audio/speech` returns a
# real RIFF WAV — but the response's `Content-Type` says `audio/mpeg` (a LiteLLM quirk)
# and the `voice` field is REQUIRED (omitting it is a 500) while its VALUE is ignored.
# Every test below pins one of those facts, so the next person to touch this client can
# see which oddities are load-bearing. The end-to-end proof against the real gateway is
# sim/tests/test_live_gateway_tts.py (creds-gated).
# ============================================================================

def _wav(pcm: bytes, rate: int = 22050, channels: int = 1, width: int = 2) -> bytes:
    """A real RIFF/WAVE container around `pcm`, written by the stdlib."""
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class _FakeSpeech:
    """A stand-in for `client.audio.speech`, recording the kwargs it was called with.

    `content_type` is deliberately the WRONG one our gateway sends, so any code that
    started branching on the header would have to notice it here first.
    """

    def __init__(self, body: bytes, content_type: str = "audio/mpeg"):
        self.body, self.content_type, self.calls = body, content_type, []
        self.audio = self.speech = self

    def create(self, **kw):
        self.calls.append(kw)
        return type("R", (), {"content": self.body,
                              "headers": {"content-type": self.content_type}})()


def _voice_synth(body, **kw):
    from moxie_sdk.tts import OpenAIVoiceSynthesizer
    fake = _FakeSpeech(body)
    return OpenAIVoiceSynthesizer("", "", client=fake, **kw), fake


def test_wav_reply_is_unwrapped_and_the_rate_comes_from_the_header():
    """The gateway's `piper-amy` answers 22050 Hz / mono / 16-bit WAV. The synthesizer
    must hand the runtime raw PCM and the file's OWN rate — so a CloudTTSResponse still
    carries the truth if the voice (and its rate) changes under us."""
    pcm = b"\x01\x02" * 100
    synth, fake = _voice_synth(_wav(pcm, rate=22050), model="piper-amy",
                               response_format="wav", sample_rate=24000)
    assert synth.sample_rate == 24000               # the CONFIGURED rate, before any call
    assert synth.synthesize("Hello from Moxie") == pcm
    assert synth.sample_rate == 22050 and synth.channels == 1   # …the header's, after
    assert fake.calls[0]["response_format"] == "wav"


def test_wav_rate_is_derived_not_assumed():
    """The same client, a 16 kHz stereo voice: nothing was configured for it and the
    response still describes itself correctly."""
    pcm = b"\x00\x01\x02\x03" * 50                  # 2 channels × 16-bit
    synth, _ = _voice_synth(_wav(pcm, rate=16000, channels=2), model="piper-amy",
                            response_format="wav", sample_rate=22050)
    assert synth.synthesize("hi") == pcm
    assert synth.sample_rate == 16000 and synth.channels == 2


def test_content_type_is_never_consulted_only_the_riff_magic():
    """Our gateway labels a valid WAV `audio/mpeg`. Trust the bytes: the same body is
    unwrapped whatever the header claims, and a body with no RIFF magic is passed
    through as PCM even when the header says `audio/wav`."""
    from moxie_sdk.tts import OpenAIVoiceSynthesizer
    pcm = b"\x07\x08" * 20
    for content_type in ("audio/mpeg", "audio/wav", "application/octet-stream", ""):
        fake = _FakeSpeech(_wav(pcm, rate=22050), content_type=content_type)
        synth = OpenAIVoiceSynthesizer("", "", client=fake, model="piper-amy",
                                       response_format="wav")
        assert synth.synthesize("hi") == pcm and synth.sample_rate == 22050
    lying = _FakeSpeech(b"\x09\x0a" * 20, content_type="audio/wav")   # raw PCM, wav header
    synth = OpenAIVoiceSynthesizer("", "", client=lying, response_format="pcm",
                                   sample_rate=22050)
    assert synth.synthesize("hi") == b"\x09\x0a" * 20 and synth.sample_rate == 22050


def test_pcm_reply_uses_the_configured_rate():
    """`response_format="pcm"` is raw 16-bit frames — nothing in the payload can say what
    rate they are, so the configured one is the only answer."""
    raw = b"\x10\x11" * 64
    synth, fake = _voice_synth(raw, model="piper-amy", response_format="pcm",
                               sample_rate=22050)
    assert synth.synthesize("hi") == raw
    assert synth.sample_rate == 22050 and synth.channels == 1
    assert fake.calls[0]["response_format"] == "pcm"


def test_voice_field_is_always_sent_and_defaults_to_the_model_suffix():
    """The gateway 500s when `voice` is missing and ignores it when present — the model
    picks the Piper voice. So: always send one, and make it the model's own suffix."""
    from moxie_sdk.tts import voice_for_model
    assert voice_for_model("piper-amy") == "amy"
    assert voice_for_model("piper-ryan") == "ryan"
    assert voice_for_model("tts-1") == "alloy"          # no word-suffix → OpenAI's default
    assert voice_for_model("") == "alloy"

    for model, expected in (("piper-amy", "amy"), ("piper-ryan", "ryan")):
        synth, fake = _voice_synth(_wav(b"\x00\x00" * 8), model=model,
                                   response_format="wav")
        synth.synthesize("hi")
        assert fake.calls[0]["voice"] == expected
        assert fake.calls[0]["model"] == model

    # an explicit voice wins, and a per-call one wins over that
    synth, fake = _voice_synth(_wav(b"\x00\x00" * 8), voice="nova", model="piper-amy",
                               response_format="wav")
    synth.synthesize("hi")
    synth.synthesize("hi", voice="shimmer")
    assert [c["voice"] for c in fake.calls] == ["nova", "shimmer"]


def test_json_instead_of_audio_is_a_clear_error():
    """An unknown model name comes back as the 400 body, not audio. Handing those bytes
    to a speaker would be a burst of noise in a child's ear."""
    from moxie_sdk.tts import VoiceServerError, pcm_from_audio
    body = (b'{"error":{"message":"Invalid model name passed in model=piper-nope",'
            b'"type":"invalid_request_error"}}')
    synth, _ = _voice_synth(body, model="piper-nope", response_format="wav")
    with pytest.raises(VoiceServerError) as err:
        synth.synthesize("hi")
    assert "Invalid model name" in str(err.value)
    # …and the low-level helper is the same story for an empty body / a broken WAV
    with pytest.raises(VoiceServerError):
        pcm_from_audio(b"", sample_rate=22050)
    with pytest.raises(VoiceServerError):
        pcm_from_audio(b"RIFF\x00\x00\x00\x00WAVEnope", sample_rate=22050)
    with pytest.raises(VoiceServerError) as err8:
        pcm_from_audio(_wav(b"\x00" * 16, width=1), sample_rate=22050)
    assert "16-bit" in str(err8.value)


def test_a_json_shaped_pcm_burst_is_still_audio():
    """The JSON sniff must not eat audio that happens to start with `{`: it only fires
    when the body actually PARSES as JSON."""
    from moxie_sdk.tts import pcm_from_audio
    raw = b"{\x00\x01\x02\x03" * 8                       # 0x7b is a legal PCM sample byte
    assert pcm_from_audio(raw, sample_rate=22050) == (raw, 22050, 1)


def test_voice_synth_still_backs_off_around_the_wav_parsing():
    """Backoff wiring survives the decode step: two 429s, then a WAV, one PCM answer.
    (A 429 cannot be forced on demand against the live gateway — this fake is the proof
    that path is wired, and the live test asserts the happy one.)"""
    from moxie_sdk.tts import OpenAIVoiceSynthesizer

    class _RateLimit(Exception):
        status_code = 429

    pcm = b"\x05\x06" * 32

    class _Flaky(_FakeSpeech):
        def create(self, **kw):
            self.calls.append(kw)
            if len(self.calls) < 3:
                raise _RateLimit()
            return type("R", (), {"content": self.body})()

    from moxie_sdk.chat import Pacer
    fake = _Flaky(_wav(pcm, rate=22050))
    synth = OpenAIVoiceSynthesizer("", "", client=fake, model="piper-amy",
                                   response_format="wav",
                                   pacer=Pacer(sleep=lambda s: None),
                                   sleep=lambda s: None)
    out = synth.synthesize("hello")     # instant: both sleeps are injected above
    assert out == pcm and len(fake.calls) == 3 and synth.sample_rate == 22050


# --- the standby voice: a gateway hiccup must never be silence ---------------

class _Boom(Synthesizer):
    name = "boom"
    sample_rate = 22050

    def __init__(self, exc=None):
        self.exc, self.calls = exc or RuntimeError("gateway said no"), 0

    def synthesize(self, text, voice=None):
        self.calls += 1
        raise self.exc


def test_fallback_speaks_with_the_standby_and_says_so_once():
    from moxie_sdk.tts import FallbackSynthesizer, ToneSynthesizer, VoiceServerError
    logged = []
    primary = _Boom(VoiceServerError("the voice server returned JSON, not audio: nope"))
    fb = FallbackSynthesizer(primary, ToneSynthesizer(sample_rate=22050),
                             log=logged.append)
    assert fb.voice_name == "boom" and not fb.failed
    first = fb.synthesize("Hi Sam, do you want to hear a story?")
    assert first, "a gateway failure must not leave the child with silence"
    assert fb.failed and fb.voice_name == "tone" and fb.sample_rate == 22050
    assert len(logged) == 1 and "boom" in logged[0] and "tone" in logged[0]
    # latched: the dead primary is never called again, and nothing new is logged
    assert fb.synthesize("and another line")
    assert primary.calls == 1 and len(logged) == 1


def test_describe_names_the_voice_that_is_actually_speaking():
    """`[run] server voice enabled: fallback` would tell an owner nothing. The startup
    log has to name the voice AND its standby, and change once it downgrades."""
    from moxie_sdk.tts import FallbackSynthesizer, ToneSynthesizer
    assert ToneSynthesizer().describe() == "tone"
    fb = FallbackSynthesizer(_Boom(), ToneSynthesizer(), log=lambda m: None)
    assert fb.describe() == "boom (standby: tone)"
    fb.synthesize("hi")
    assert fb.describe() == "tone (standby — boom failed)"


def test_fallback_is_a_passthrough_while_the_primary_works():
    from moxie_sdk.tts import FallbackSynthesizer, ToneSynthesizer
    pcm = b"\x02\x03" * 40
    primary, fake = _voice_synth(_wav(pcm, rate=16000), model="piper-amy",
                                 response_format="wav")
    fb = FallbackSynthesizer(primary, ToneSynthesizer())
    resp = synthesize_cloud_tts(fb, '<mark name="cmd:x"/>Hello there', event_id="g1")
    assert base64.b64decode(resp["audio"]["buffer"]) == pcm
    assert resp["audio"]["sample_rate"] == 16000      # the wav header's rate rode through
    assert fake.calls[0]["input"] == "Hello there" and not fb.failed
