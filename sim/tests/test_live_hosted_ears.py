"""
Live HOSTED EARS — real spoken words through `functions/api/transcribe.js`, the route a
visitor's microphone actually posts to.

=============================================================================
WHAT THIS PROVES, AND — MORE IMPORTANTLY — WHAT IT DOES NOT.

It proves: **the shipped transcribe route accepts real speech bytes, in the exact
container `sim/web/mic.js` encodes (16 kHz mono RIFF/WAVE, raw body, `audio/wav`), makes
exactly one upstream call, and returns the words that were spoken.**

It does **not** prove that a human's microphone works in a browser. **No person has ever
spoken into the microphone on the hosted site**, and this file does not change that. The
audio here is synthesised, not captured; `getUserMedia`, the permission prompt, the
`AudioWorklet` capture graph, `mic.js::encodeWav`'s resample of a real device's 48 kHz,
and whatever a child's actual voice does to an ASR are all still unproven. What is left
after this file is named in full at the bottom of this docstring, and in the doc row this
slice touched. **This narrows the human-voice gap; it does not close it.**

=============================================================================
THE GAP THIS FILLS, in one paragraph. Three files already circle it and none lands on it.
`sim/test_demo_ears.mjs` calls the REAL `transcribe.js` with a stubbed `fetch` and no key,
so every one of its assertions would still pass if the gateway transcribed everything as
"banana". `sim/test_mic_spend.mjs` drives the real page in Chrome but answers `/api/*` at
the browser, so nothing it sends leaves the machine and its audio is a 440 Hz tone.
`sim/tests/test_live_gateway_stt.py` does put real speech through a real gateway at word
overlap 1.00 — but through `moxie_sdk.stt`, the PYTHON seam, never through the route. So
the route that the public page depends on had never carried a spoken word. That is the
single largest untested surface on the live page, and it is what this file tests.

=============================================================================
TWO TIERS, because "the hosted route" has two honest meanings:

  TIER A — `test_the_shipped_transcribe_route_hears_real_speech`. The route MODULE, run in
  node against the REAL gateway (`sim/tests/helpers_route.mjs`). This is the code the
  deployment runs, with real credentials, making a real call. It runs anywhere the gateway
  creds are, CI included, and is the tier the deep workflow dispatches.

  TIER B — `test_the_deployed_origin_hears_real_speech`. The same WAV POSTed over the
  network to a REAL DEPLOYMENT (`MOXIE_DEMO_ORIGIN`), which additionally exercises
  Cloudflare's runtime, the deployment's own env bindings, and the origin pin as it is
  actually configured. Skips when the variable is unset, saying so.

Tier A is the code; tier B is the site. Neither is the human.

=============================================================================
WHERE THE SPEECH COMES FROM, and why that is not circular. The sentence is spoken by the
gateway's own voice through `config.build_synthesizer()` — the idiom
`test_live_gateway_stt.py` established and `docs/architecture/implementation-plan.md`
records as **word overlap 1.00 for gateway TTS → gateway STT at both 22050 Hz and 16 kHz**
(the AI-seam STT/TTS rows, and the 2026-09-02 entry). Synthesised speech is real speech as
far as this route is concerned: it is broadband voiced audio with formants and silences,
not the placeholder tone (asserted below via `helpers_audio.is_real_speech_stdlib` — the
numpy-free twin, so the check holds on the numpy-free box this file is about, and not only
where a Whisper wheel happens to have dragged numpy in). What it is *not* is a child in a
room with a laptop microphone — see the top of this docstring.

The two backends being the same vendor is a real caveat and it is why the DECOY CONTROL
below exists: a gateway that simply echoed its own synthesis input would also score 1.00,
so the test additionally requires the SAME transcript to score near zero against a
different sentence. An echo cannot pass both.

=============================================================================
BUDGET — 3 gateway calls for a full run: 1 `/audio/speech` (shared by both tiers via a
module-scoped fixture, so tier B costs no extra synthesis) + 1 `/audio/transcriptions` per
tier. Tier A asserts `upstream_calls == 1` from `_lib/limits.js`'s own counter rather than
trusting the count.

WHAT REMAINS UNPROVEN ABOUT A HUMAN USING THE MICROPHONE, precisely:
  1. no human has spoken into the hosted page — not once;
  2. `getUserMedia` and the browser permission prompt on the deployed origin;
  3. `mic.js::encodeWav` on a REAL device's sample rate (48 kHz from a laptop) rather than
     on the 22050 Hz this file hands it;
  4. a child's voice, room noise, clipping, and distance from the microphone;
  5. the 15-second hard stop against a real recorder (hermetically covered in
     `sim/test_demo_ears.mjs` Part B with a fake recorder, never against a device).

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

#: The gateway the route will be pointed at. Same resolution order as
#: `test_live_gateway_stt.py`, so one `mqtt/.env` runs both.
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

#: A real deployment to POST at, e.g. `https://moxie.mattvalancy.com`. Deliberately NOT
#: defaulted to any host: C3 of the live-demo spec says nothing in this repo hard-codes a
#: deployment, and a test that silently pointed at the owner's domain would be exactly
#: that. Unset means tier B skips and says what went unproven.
DEMO_ORIGIN = (os.environ.get("MOXIE_DEMO_ORIGIN") or "").strip().rstrip("/")

pytestmark = pytest.mark.skipif(
    not (BASE and KEY),
    reason="no gateway configured — NOTHING about the hosted ears was proven by this run "
           "(set MOXIE_VOICE_BASE_URL / MOXIE_STT_BASE_URL + a key in mqtt/.env)")

# --------------------------------------------------------------------------- #
# The sentence, and the sentence it is NOT.
# --------------------------------------------------------------------------- #
#: 13 words of ordinary child-facing English. No proper nouns an ASR has never heard
#: except "Moxie", which is the one word this product cannot avoid and which the gateway
#: has already been measured hearing correctly.
SPOKEN_LINE = "Hi Moxie, I built a really tall tower out of blue blocks today."

#: The DECOY. A different, equally ordinary sentence that shares no content word with the
#: line above. Its only role is to be scored against the SAME transcript, so the test
#: measures the distance between "the right words" and "any words" instead of assuming it.
#: Without this, a route that returned a fixed plausible sentence — or a gateway that
#: echoed its own TTS input — would be indistinguishable from one that listened.
DECOY_LINE = "Please tell me a story about the sleepy purple dragon who lost his shoes."

#: THE FLOOR, chosen deliberately and not by taste.
#:
#:  * The measured value on this exact pair of backends is **1.00** — gateway TTS read back
#:    by gateway STT, at 22050 Hz and at 16 kHz, recorded in
#:    `docs/architecture/implementation-plan.md` (AI-seam STT row; 2026-09-02 entry).
#:  * 0.7 is what `test_live_gateway_stt.py::STT_FLOOR` already requires of the same round
#:    trip. Two different floors for one claim would be two floors that mean nothing, so
#:    this file adopts that number rather than inventing a second one.
#:  * On a 13-word line it means **10 of 13 words**, which no unrelated transcript reaches:
#:    the decoy scores ~0.07 against a correct transcript, and `DECOY_CEIL` pins that.
#:  * It is not 1.00 on purpose. A model update that hears "Moxie" as "Moxy" or drops the
#:    trailing "today" is not the failure this file exists to catch, and a brittle floor
#:    would redden a working microphone path for a reason nobody could act on.
STT_FLOOR = 0.7

#: THE CEILING FOR A WRONG SENTENCE. Half the floor: enough room for the stopword
#: collisions any two English sentences share ("a", "the"), nowhere near what a correct
#: transcript scores. This is the assertion that makes the floor non-vacuous IN CI, on
#: every run — not only in a one-off mutation experiment.
DECOY_CEIL = 0.35

#: `sim/web/mic.js`:363 — *"The rate that matters is 16000"*. The page encodes 16 kHz mono
#: WAV before it uploads, so that is what this test uploads.
UPLOAD_RATE = A.ROBOT_SAMPLE_RATE

#: A BROWSER-SHAPED USER-AGENT, and it is not cosmetic — this is a finding, made free of
#: charge on 2026-09-05 with a sub-floor clip that the route refuses without calling
#: anything. **A default `Python-urllib/3.x` request to the production origin never reaches
#: the Function at all**: Cloudflare answers `403` with *"error code: 1010"*, the browser
#: integrity check, from the edge. The same bytes with the User-Agent below get the route's
#: own `400 too_short`, which is the answer that proves the origin pin admitted us.
#:
#: And the block page is not obviously a block page: with `Accept: application/json` the
#: edge answers RFC-7807 problem details (`error_code: 1010`,
#: `error_name: browser_signature_banned`) — valid JSON, no `reason` field. Parsed as our
#: envelope that reads as `403 reason=None`, a Function refusing speech for no stated
#: cause, which is why tier B checks for `reason` before it checks the status.
#:
#: Two things follow, and both matter more than this constant does:
#:   * a tier-B failure has TWO possible authors now — our route, and the edge in front of
#:     it — so the test distinguishes them by reason string rather than by status alone;
#:   * anyone writing a non-browser client against a deployment of this site (a probe, a
#:     health checker, a robot posting audio directly) needs a real User-Agent or they will
#:     get a 1010 with no envelope, no `reason`, and nothing in the Function's logs.
#: It is a plausible desktop-Chrome string and nothing else: no cookies, no forged
#: `Sec-Fetch-*` (the pin does not need them — §4.3 requires `Sec-Fetch-Site` only WHEN
#: PRESENT), and the `Origin` header is honestly this deployment's own.
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0.0.0 Safari/537.36")

HARNESS = os.path.join(os.path.dirname(__file__), "helpers_route.mjs")


# --------------------------------------------------------------------------- #
# The audio: one synthesis, shared by both tiers.
# --------------------------------------------------------------------------- #
def _synthesizer():
    """`config.build_synthesizer()` on the gateway voice — the shipped switch, and the
    environment is put back exactly as it was.

    The restore is not tidiness. `test_env_hygiene_live_suites.py` documents a day spent
    on nine red tests caused by one live suite leaving `MOXIE_STT`/`MOXIE_APP` set for the
    next one; engine selectors are read by every later `config` reload in the session.
    """
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


#: An already-rendered WAV of `SPOKEN_LINE` to upload instead of synthesising a new one.
#:
#: Live suites here are budgeted in whole gateway calls — `sim/tools/probe_demo_gateway.mjs`
#: carries `--only=` for exactly this reason (*"re-verifying ONE fixed body must not cost
#: four calls"*) — and this file's proof needs a transcription far more often than it needs
#: a new recording: a failing tier, a threshold argument, a mutation run. Pointing this at
#: the WAV a previous run wrote halves the cost of every one of those.
#:
#: It cannot make the test pass dishonestly. Whatever it names still has to clear the
#: RIFF/size/duration checks, still has to be broadband speech, and — the point — still has
#: to TRANSCRIBE BACK to `SPOKEN_LINE` at the floor. Aim it at the wrong audio and the file
#: goes red, which is the mutation this slice was asked to demonstrate.
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
    """ONE `/audio/speech` call for the whole file: the sentence, as the 16 kHz mono WAV
    the browser would have uploaded, plus the temp file the two tiers post.

    Module-scoped on purpose — the budget for this slice is five gateway calls, and a
    per-test synthesis would spend two of them saying the same thing twice.
    """
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
    # Down to the rate the page uploads at, with the standard library alone: a hosted box
    # that installed nothing but `openai` has to be able to run this file.
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
    """`(right, wrong)` — the transcript scored against the sentence, and against the
    decoy. Both are printed by every caller, because a number nobody can see is a number
    nobody can check."""
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
    """The key and the gateway host must not appear in anything the route hands back.
    `sim/test_demo_ears.mjs` proves this against `sk-testonly-…`; a proof against a fake
    key is not a proof against the real one, which is what is in the room here."""
    for secret, label in ((KEY, "the gateway key"), (BASE, "the gateway base URL")):
        if secret:
            assert secret not in blob, f"{where} leaked {label}"


# --------------------------------------------------------------------------- #
# 0. the audio is speech, not the placeholder tone
# --------------------------------------------------------------------------- #
def test_the_audio_we_upload_is_actually_speech(spoken):
    """A tone would sail through every structural check in this file and transcribe to
    nothing, so the audio is checked before it is trusted.

    This used to be `is_real_speech` inside a `try: … except ImportError: pytest.skip(…)`,
    because the predicate needed numpy and THIS FILE MUST RUN WITHOUT IT — the deployment
    it proves is a hosted box that installed nothing but `openai`. So on exactly the
    machine shape the file exists for, its first assertion skipped. Since 2026-09-05 the
    measurement has a stdlib twin (`helpers_audio.spectral_flatness_stdlib`, verdict-equal
    to the numpy one and asserted so hermetically by `test_speech_guard.py`), so the check
    is unconditional everywhere: tone ~8e-10, a real voice ~1e-2, floor 1e-6."""
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
    """`functions/api/transcribe.js::onRequestPost` — the exact code the deployment runs —
    fed the exact bytes `mic.js` uploads, pointed at the real gateway.

    Everything between is the shipped path and none of it is stubbed: the origin pin, the
    byte floor and ceiling, the magic-number sniff, the `DEMO_STT_FORMATS` allowlist, the
    WAV duration cap read out of our own header, the server-fixed model, the multipart
    body, and `cleanTranscript`. One gateway call, counted by the route's own counter."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH — the shipped route was NOT exercised and "
                    "NOTHING about the hosted ears was proven by this run")

    env = dict(os.environ)
    env.update({
        "DEMO_GATEWAY_BASE_URL": BASE,
        "DEMO_GATEWAY_API_KEY": KEY,
        "DEMO_STT_MODEL": STT_MODEL,
        # THE EARS NEED A BRAIN MODEL TO EXIST, and that is not obvious from this route.
        # `_lib/env.js::readConfig` puts `DEMO_CHAT_MODEL` in `missing` when it is unset,
        # `configured` is false, and `modeOf` answers `gateway_not_configured` — so a
        # deployment with a transcriber and no chat model has NO EARS, whatever
        # `DEMO_STT_MODEL` says. Found the expensive way: the first run of this test
        # against the real gateway got a 503 and spent zero calls doing it. The value is
        # never used by this route; it only has to be present.
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
    """The same WAV, POSTed at a running deployment exactly as `sim/web/mic.js` posts it:
    raw body, `Content-Type: audio/wav`, `Origin` set to the site's own origin (§4.3 —
    `Sec-Fetch-Site` is only required WHEN PRESENT, so a non-browser client that sets a
    matching `Origin` is admitted).

    This is the tier that also exercises Cloudflare's runtime, the deployment's own env
    bindings and its configured origin pin — none of which tier A touches."""
    if not DEMO_ORIGIN:
        pytest.skip("MOXIE_DEMO_ORIGIN unset — NOTHING was proven about a real "
                    "deployment's ears by this run (set it to a deployed origin, e.g. "
                    "the production Pages domain, to spend one transcription there)")

    # The health probe first: it costs no gateway call and it is the difference between
    # "the deployment cannot hear" and "the deployment has no ears configured".
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
    # WHOSE 403 IS THIS? The question is not rhetorical, and JSON is not the answer:
    # dropping `BROWSER_UA` above gets a 403 whose body is *also* `application/json` —
    # Cloudflare's own problem-details for `error_code: 1010`, which honours our `Accept`
    # header. Read as our envelope it looks like `reason: None`, i.e. a route that refused
    # speech for no stated cause, and an operator would go debugging a Function that never
    # ran. Every envelope this site emits carries `reason` (null on success), so its
    # ABSENCE is the tell, and it is worth more than the status code.
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
