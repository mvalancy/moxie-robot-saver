"""
Live HOSTED EARS — real spoken words through `functions/api/transcribe.js`, the route a
visitor's microphone posts to.

PROVES: the shipped route accepts real speech in the exact container `sim/web/mic.js`
encodes (16 kHz mono WAV, raw body, `audio/wav`), makes exactly one upstream call, and
returns the words that were spoken. The neighbours stop short of that:
`sim/test_demo_ears.mjs` stubs `fetch`, `sim/test_mic_spend.mjs` answers `/api/*` at the
browser with a tone, and `test_live_gateway_stt.py` goes through the Python seam, never
the route.

  TIER A — the route MODULE in node against the REAL gateway (`helpers_route.mjs`): the
  code the deployment runs, wherever gateway creds are (the deep tier dispatches it).
  TIER B — the same WAV POSTed to a real deployment (`MOXIE_DEMO_ORIGIN`), which adds
  Cloudflare's runtime, env bindings and the origin pin. Skips, saying so, when unset.

The speech is the gateway's own TTS (`config.build_synthesizer()`), checked to be
broadband speech rather than the placeholder tone. Because TTS and STT share a vendor,
the transcript must also score near zero against a DECOY sentence — an echo cannot pass
both. Budget: 1 `/audio/speech` (module-scoped, or 0 with `MOXIE_EARS_WAV`) + 1
transcription per tier; tier A asserts `upstream_calls == 1` from the route's own counter.

NOT PROVEN (no human has spoken into the hosted page): `getUserMedia` and the permission
prompt; `mic.js::encodeWav` on a real device's 48 kHz; a child's voice, room noise and
distance; the 15-second stop against a real recorder.

    MOXIE_DEMO_ORIGIN=https://… .venv/bin/python -m pytest \
        sim/tests/test_live_hosted_ears.py -q -s
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MQTT = os.path.join(REPO, "mqtt")
sys.path.insert(0, MQTT)
sys.path.insert(0, os.path.dirname(__file__))

pytest.importorskip("openai", reason="openai SDK not installed (live hosted-ears test)")

import helpers_audio as A                                    # noqa: E402
from helpers_runtime import load_repo_dotenv                 # noqa: E402

load_repo_dotenv()          # mqtt/.env from this tree or the main checkout

#: Same resolution order as `test_live_gateway_stt.py`, so one `mqtt/.env` runs both.
BASE = (os.environ.get("MOXIE_STT_BASE_URL")
        or os.environ.get("MOXIE_VOICE_BASE_URL")
        or "").strip()
KEY = (os.environ.get("MOXIE_STT_API_KEY")
       or os.environ.get("MOXIE_VOICE_API_KEY")
       or os.environ.get("MOXIE_LLM_API_KEY")
       or os.environ.get("LITELLM_MASTER_KEY") or "")
STT_MODEL = (os.environ.get("MOXIE_STT_MODEL") or "stt-whisper").strip()
CHAT_MODEL = (os.environ.get("MOXIE_LLM_MODEL") or "graphling-medium").strip()
VOICE_BASE = (os.environ.get("MOXIE_VOICE_BASE_URL") or BASE).strip()
TTS_MODEL = (os.environ.get("MOXIE_VOICE_MODEL") or "piper-amy").strip()

#: A real deployment to POST at. Never defaulted: nothing in this repo hard-codes a
#: deployment (live-demo spec C3). Unset means tier B skips.
DEMO_ORIGIN = (os.environ.get("MOXIE_DEMO_ORIGIN") or "").strip().rstrip("/")

pytestmark = pytest.mark.skipif(
    not (BASE and KEY),
    reason="no gateway configured — NOTHING about the hosted ears was proven by this run "
           "(set MOXIE_VOICE_BASE_URL / MOXIE_STT_BASE_URL + a key in mqtt/.env)")

# --------------------------------------------------------------------------- #
# The sentence, and the sentence it is NOT.
# --------------------------------------------------------------------------- #
#: 13 words of ordinary child-facing English; "Moxie" is the one unavoidable proper noun.
SPOKEN_LINE = "Hi Moxie, I built a really tall tower out of blue blocks today."

#: The DECOY: no shared content word, scored against the SAME transcript, so a route that
#: returned a fixed plausible sentence (or echoed its TTS input) cannot pass.
DECOY_LINE = "Please tell me a story about the sleepy purple dragon who lost his shoes."

#: The same floor `test_live_gateway_stt.py::STT_FLOOR` requires of this round trip
#: (measured 1.00): 10 of 13 words. Not 1.00, so "Moxy" for "Moxie" does not redden a
#: working microphone path.
STT_FLOOR = 0.7

#: Half the floor: room for shared stopwords, nowhere near a correct transcript — the
#: assertion that keeps the floor non-vacuous on every run.
DECOY_CEIL = 0.35

#: `sim/web/mic.js` encodes 16 kHz mono WAV before upload, so that is what is uploaded.
UPLOAD_RATE = A.ROBOT_SAMPLE_RATE

#: Not cosmetic: a default `Python-urllib` request never reaches the Function — the edge
#: answers 403 "error code: 1010" (browser integrity check, plain text or RFC-7807 JSON),
#: never with our `reason` field. The check refuses some user agents, not every non-browser
#: one (spec §10, assumption 30), and a `POST` from the others is unmeasured, so this test
#: sends a browser User-Agent. No cookies or forged `Sec-Fetch-*`; the `Origin` is honestly
#: the deployment's own.
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0.0.0 Safari/537.36")

HARNESS = os.path.join(os.path.dirname(__file__), "helpers_route.mjs")


# --------------------------------------------------------------------------- #
# The audio: one synthesis, shared by both tiers.
# --------------------------------------------------------------------------- #
def _synthesizer():
    """`config.build_synthesizer()` on the gateway voice, restoring the environment
    exactly: engine selectors leaked by one live suite redden later `config` reloads."""
    keys = ("MOXIE_TTS", "MOXIE_VOICE_BASE_URL", "MOXIE_VOICE_MODEL", "MOXIE_VOICE_FORMAT",
            "MOXIE_VOICE_SAMPLE_RATE", "MOXIE_PIPER_MODEL", "MOXIE_STT", "MOXIE_APP")
    keep = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ.pop(k, None)
        os.environ["MOXIE_VOICE_BASE_URL"] = VOICE_BASE
        os.environ["MOXIE_VOICE_MODEL"] = TTS_MODEL
        os.environ["MOXIE_VOICE_FORMAT"] = "wav"
        import config as _c
        module = importlib.reload(_c)
        from moxie_sdk.tts import FallbackSynthesizer
        synth = module.build_synthesizer()
        assert isinstance(synth, FallbackSynthesizer), synth
        return synth
    finally:
        for k, v in keep.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


#: An already-rendered 16 kHz WAV of `SPOKEN_LINE` to reuse instead of synthesising (saves
#: a gateway call on re-runs). Cannot pass dishonestly: it must still be speech and still
#: transcribe back to `SPOKEN_LINE` at the floor.
EARS_WAV = (os.environ.get("MOXIE_EARS_WAV") or "").strip()


def _wav_pcm(raw):
    """`(pcm16, rate)` out of a RIFF/WAVE file, stdlib only."""
    import io
    import wave
    with wave.open(io.BytesIO(raw), "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2, "mono PCM16 only"
        return w.readframes(w.getnframes()), w.getframerate()


@pytest.fixture(scope="module")
def spoken():
    """ONE `/audio/speech` call for the whole file: the sentence as the 16 kHz mono WAV the
    browser would upload, plus the temp file both tiers post."""
    from moxie_sdk.stt import wav_bytes
    if EARS_WAV:
        raw = open(EARS_WAV, "rb").read()
        pcm16, rate = _wav_pcm(raw)
        assert rate == UPLOAD_RATE, (
            f"MOXIE_EARS_WAV is {rate} Hz; the page uploads {UPLOAD_RATE} Hz")
        print(f"\n[ears] said={SPOKEN_LINE!r} (NOT synthesised this run — reusing "
              f"{EARS_WAV}, {len(raw)} B, {A.duration_s(pcm16, rate):.2f}s @ {rate} Hz)")
        return {"pcm": pcm16, "wav": raw, "path": EARS_WAV, "native_rate": rate}
    synth = _synthesizer()
    with A.Stage("tts") as t:
        pcm = synth.synthesize(SPOKEN_LINE)
    assert not synth.failed, "the gateway voice fell through to the standby — no speech"
    assert synth.voice_name == "openai-voice", synth.voice_name
    native_rate = synth.sample_rate
    # stdlib resample: a hosted box with only `openai` installed must run this file
    pcm16 = A.resample_pcm16_stdlib(pcm, native_rate, UPLOAD_RATE)
    wav = wav_bytes(pcm16, UPLOAD_RATE)
    path = os.path.join(tempfile.mkdtemp(prefix="moxie-ears-"), "utterance.wav")
    with open(path, "wb") as fh:
        fh.write(wav)
    print(f"\n[ears] said={SPOKEN_LINE!r} voice={TTS_MODEL} tts={t.seconds:.2f}s"
          f"\n[ears] native={native_rate} Hz -> upload={UPLOAD_RATE} Hz"
          f" wav={len(wav)} B audio={A.duration_s(pcm16, UPLOAD_RATE):.2f}s")
    return {"pcm": pcm16, "wav": wav, "path": path, "native_rate": native_rate}


def _overlaps(heard):
    """`(right, wrong)`: the transcript scored against the sentence and the decoy."""
    return A.word_overlap(SPOKEN_LINE, heard), A.word_overlap(DECOY_LINE, heard)


def _assert_words(heard, *, where):
    """The one assertion this whole file exists for, and its negative control."""
    right, wrong = _overlaps(heard)
    print(f"[ears] {where} heard={heard!r}\n[ears] {where} overlap={right:.2f}"
          f" decoy={wrong:.2f} (floor {STT_FLOOR}, decoy ceiling {DECOY_CEIL})")
    assert heard.strip(), f"{where} returned an EMPTY transcript for real speech"
    assert right >= STT_FLOOR, (
        f"{where} recovered only {right:.2f} of the words\n"
        f"  said : {SPOKEN_LINE!r}\n  heard: {heard!r}")
    assert wrong < DECOY_CEIL, (
        f"{where} scored {wrong:.2f} against a sentence that was NEVER SPOKEN — the "
        f"overlap measure is not discriminating, so the floor above proves nothing\n"
        f"  decoy: {DECOY_LINE!r}\n  heard: {heard!r}")
    return right, wrong


def _assert_no_credential(blob, *, where):
    """The REAL key and gateway host must not appear in anything the route returns
    (`sim/test_demo_ears.mjs` only proves it against a fake key)."""
    for secret, label in ((KEY, "the gateway key"), (BASE, "the gateway base URL")):
        if secret:
            assert secret not in blob, f"{where} leaked {label}"


# --------------------------------------------------------------------------- #
# 0. the audio is speech, not the placeholder tone
# --------------------------------------------------------------------------- #
def test_the_audio_we_upload_is_actually_speech(spoken):
    """A tone would pass every structural check and transcribe to nothing, so the audio is
    checked first — with the stdlib twin, so it holds on the numpy-free box this file is
    about (tone ~1e-9, voice ~1e-2, floor 1e-6)."""
    assert len(spoken["wav"]) > 2000, "under DEMO_MIN_AUDIO_BYTES — the route would refuse it free"
    assert len(spoken["wav"]) < 500000, "over DEMO_MAX_AUDIO_BYTES — the route would refuse it free"
    assert A.duration_s(spoken["pcm"], UPLOAD_RATE) < 15.0, "over DEMO_MAX_RECORD_MS"
    assert spoken["wav"][:4] == b"RIFF" and spoken["wav"][8:12] == b"WAVE"
    flat = A.spectral_flatness_stdlib(spoken["pcm"])
    print(f"[ears] spectral flatness {flat:.3e} (floor {A.SPEECH_FLATNESS_FLOOR:.0e})")
    assert A.is_real_speech_stdlib(spoken["pcm"]), (
        f"the audio about to be uploaded is tone-shaped ({flat:.3e}) — this test would "
        f"have proven nothing about speech")


# --------------------------------------------------------------------------- #
# TIER A — the shipped route module, against the real gateway
# --------------------------------------------------------------------------- #
def test_the_shipped_transcribe_route_hears_real_speech(spoken):
    """`transcribe.js::onRequestPost`, fed the bytes `mic.js` uploads, pointed at the real
    gateway. Nothing between is stubbed: origin pin, byte floor/ceiling, magic sniff,
    format allowlist, WAV duration cap, server-fixed model, multipart body,
    `cleanTranscript`. One gateway call, counted by the route's own counter."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH — the shipped route was NOT exercised and "
                    "NOTHING about the hosted ears was proven by this run")

    env = dict(os.environ)
    env.update({
        "DEMO_GATEWAY_BASE_URL": BASE,
        "DEMO_GATEWAY_API_KEY": KEY,
        "DEMO_STT_MODEL": STT_MODEL,
        # `_lib/env.js::readConfig` reports `gateway_not_configured` without a chat model,
        # so a deployment with a transcriber and no chat model has NO EARS. Unused here.
        "DEMO_CHAT_MODEL": CHAT_MODEL,
    })
    proc = subprocess.run([node, HARNESS, "transcribe", spoken["path"]],
                          env=env, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, f"harness failed: {proc.stderr[-2000:]}"
    _assert_no_credential(proc.stdout, where="the route (stdout)")
    out = json.loads(proc.stdout)
    body = json.loads(out["body"])

    print(f"\n[ears] tier A route status={out['status']} calls={out['upstream_calls']}"
          f" uploaded={out['request_bytes']} B in {out['elapsed_s']:.2f}s"
          f" model={STT_MODEL}")
    _assert_no_credential(json.dumps(out["headers"]), where="the route (headers)")

    assert out["status"] == 200, f"the route refused real speech: {body.get('reason')!r}"
    assert body["ok"] is True and body["reason"] is None, body
    assert body["mode"] == "live" and body["ears"] is True, body
    # The budget claim, from `_lib/limits.js`'s own counter rather than from hope.
    assert out["upstream_calls"] == 1, (
        f"the route spent {out['upstream_calls']} gateway calls for one utterance")
    _assert_words(body["transcript"], where="tier A (route module)")


# --------------------------------------------------------------------------- #
# TIER B — a real deployment, over the network
# --------------------------------------------------------------------------- #
def test_the_deployed_origin_hears_real_speech(spoken):
    """The same WAV POSTed at a running deployment as `mic.js` posts it (raw body,
    `audio/wav`, the site's own `Origin` — §4.3 requires `Sec-Fetch-Site` only when
    present). Also exercises Cloudflare's runtime, env bindings and origin pin."""
    if not DEMO_ORIGIN:
        pytest.skip("MOXIE_DEMO_ORIGIN unset — NOTHING was proven about a real "
                    "deployment's ears by this run (set it to a deployed origin, e.g. "
                    "the production Pages domain, to spend one transcription there)")

    # free health probe first: "cannot hear" vs "no ears configured"
    probe = urllib.request.Request(DEMO_ORIGIN + "/api/health",
                                   headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(probe, timeout=30) as r:
        health = json.loads(r.read().decode("utf-8"))
    print(f"\n[ears] tier B {DEMO_ORIGIN} mode={health.get('mode')!r} "
          f"ears={health.get('ears')} voice={health.get('voice')}")
    if not health.get("ears"):
        pytest.skip(f"{DEMO_ORIGIN} reports ears={health.get('ears')!r} — the deployment "
                    "has no transcriber configured, so NOTHING was proven about it")
    limits = health.get("limits") or {}
    assert len(spoken["wav"]) <= limits.get("max_audio_bytes", 500000), \
        "the clip is over the deployment's own published byte cap"

    req = urllib.request.Request(
        DEMO_ORIGIN + "/api/transcribe", data=spoken["wav"], method="POST",
        headers={"Content-Type": "audio/wav", "Origin": DEMO_ORIGIN,
                 "Accept": "application/json", "User-Agent": BROWSER_UA})
    with A.Stage("stt") as s:
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                status, raw = r.status, r.read().decode("utf-8")
        except urllib.error.HTTPError as e:      # a refusal still carries the envelope
            status, raw = e.code, e.read().decode("utf-8")
    _assert_no_credential(raw, where="the deployment")
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    # Every envelope this site emits carries `reason` (null on success); its ABSENCE means
    # the edge answered (e.g. Cloudflare's JSON 1010 page), not the Function.
    if not isinstance(body, dict) or "reason" not in body:
        pytest.fail(
            f"{DEMO_ORIGIN} answered {status} with something that is NOT this site's "
            f"envelope, so the request never reached the Function — it was answered by "
            f"the EDGE (a Cloudflare block page, an Access login, a proxy). "
            f"NOTHING about the deployment's ears was proven. Body: {raw[:240]!r}")
    print(f"[ears] tier B status={status} reason={body.get('reason')!r} "
          f"uploaded={len(spoken['wav'])} B in {s.seconds:.2f}s")

    assert status == 200, (
        f"{DEMO_ORIGIN} refused real speech with {status} {body.get('reason')!r} — the "
        f"hosted microphone would have fallen back to a scripted line here")
    assert body.get("reason") is None, body
    assert body["ok"] is True and body["mode"] == "live", body
    _assert_words(body.get("transcript", ""), where=f"tier B ({DEMO_ORIGIN})")
