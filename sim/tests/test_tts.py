"""
TTS seam tests (M4) — markup stripping, the CloudTTSResponse encoder, and the
synthesize flow with a fake synthesizer. Pure (no voice server); the OpenAI backend
is exercised only for availability/skip.
"""
import base64
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.tts import (  # noqa: E402
    strip_markup, Synthesizer, build_cloud_tts_response, synthesize_cloud_tts,
    make_voice_synthesizer,
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
