"""Runtime configuration for the Moxie robot-cloud supervisor.
All local-first; override via environment variables or a git-ignored `mqtt/.env`
(see .env.example — never commit real endpoints/keys)."""
import os


def _load_env():
    """Load KEY=VALUE lines from mqtt/.env into the environment (no dependency)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


_load_env()

# --- broker ---
MQTT_HOST = os.environ.get("MOXIE_MQTT_HOST", "127.0.0.1")   # supervisor→broker (loopback)
MQTT_PORT = int(os.environ.get("MOXIE_MQTT_PORT", "1883"))   # plain listener for the supervisor

# --- broker credential (security-broker-auth.md §2.2) ---
# The supervisor is the appliance's ONE fleet-wide MQTT identity — the only client that
# may read `$SYS/broker/log` (where every d_<uuid> is announced) and write into another
# device's subtree. Unset = today's behaviour exactly: an anonymous supervisor on an
# open broker, which is what a bare-metal dev broker and the SIL harness still run.
MQTT_USERNAME = os.environ.get("MOXIE_MQTT_USER", "")
# Two ways in, because a secret in `environment:` is visible to `docker inspect` and to
# anything that can read /proc: MOXIE_MQTT_PASSWORD is the literal (fine for a hand-run
# supervisor), MOXIE_MQTT_PASSWORD_FILE is a path the compose one-shot minted at 0600
# inside the shared volume. An explicit literal wins; otherwise the file is read.
MQTT_PASSWORD = os.environ.get("MOXIE_MQTT_PASSWORD", "")
MQTT_PASSWORD_FILE = os.environ.get("MOXIE_MQTT_PASSWORD_FILE", "")


def broker_credentials():
    """`(username, password)` for the supervisor's MQTT client — `("", "")` when unset.

    Read at CONNECT time rather than baked in at import, because in compose the `certs`
    one-shot may mint the secret after this module was first imported. A missing or
    unreadable password file is not fatal: it degrades to anonymous, which is exactly
    what a broker with no `password_file` expects, and the connection failure it would
    otherwise cause is far harder to diagnose than a log line.
    """
    password = MQTT_PASSWORD
    if not password and MQTT_PASSWORD_FILE:
        try:
            with open(MQTT_PASSWORD_FILE) as fh:
                password = fh.read().strip()
        except OSError as e:
            print(f"[config] MOXIE_MQTT_PASSWORD_FILE unreadable ({e.strerror}) — "
                  f"connecting anonymously", flush=True)
            password = ""
    if MQTT_USERNAME and password:
        return MQTT_USERNAME, password
    return "", ""

# Best-effort HTTP status endpoint (http://127.0.0.1:STATUS_PORT/status). Env-overridable
# so repeated/parallel SIL runs (or a leftover supervisor) don't collide on one fixed port.
STATUS_PORT = int(os.environ.get("MOXIE_STATUS_PORT", "8930"))

# The host/IP the ROBOT uses to reach the broker (goes into the endpoint QR).
BROKER_PUBLIC_HOST = os.environ.get("MOXIE_BROKER_HOST", "192.168.1.9")
BROKER_PUBLIC_PORT = int(os.environ.get("MOXIE_BROKER_PORT", "8883"))

# --- which MoxieApp drives the robot ---
# "llm" (default), "echo", "webhook", or "content"
MOXIE_APP = os.environ.get("MOXIE_APP", "llm")

# Content app: a data-driven module (conversations/globals) run through the AI seam.
CONTENT_MODULE = os.environ.get("MOXIE_CONTENT_MODULE", "content_modules/starter.json")

# LLM brain — any OpenAI-compatible endpoint. Default: our LiteLLM gateway (public
# host; the API KEY is never committed — set MOXIE_LLM_API_KEY in a git-ignored .env).
LLM_BASE_URL = os.environ.get("MOXIE_LLM_BASE_URL", "https://gateway.graphlings.net/v1")
LLM_API_KEY  = os.environ.get("MOXIE_LLM_API_KEY", os.environ.get("LITELLM_MASTER_KEY", ""))
LLM_MODEL    = os.environ.get("MOXIE_LLM_MODEL", "graphling-medium")

# AI voice server (optional) — server-side STT/TTS for the SIM + a server voice.
# OpenAI-compatible audio endpoints assumed (/audio/transcriptions, /audio/speech);
# key from MOXIE_VOICE_API_KEY (falls back to the LLM key). Empty → not configured.
VOICE_BASE_URL = os.environ.get("MOXIE_VOICE_BASE_URL", "")
VOICE_API_KEY  = os.environ.get("MOXIE_VOICE_API_KEY", LLM_API_KEY)
# Which voice the endpoint should speak with. On our LiteLLM gateway the MODEL is the
# voice ("piper-amy" / "piper-ryan"), so this is only read when the endpoint is a real
# OpenAI-shaped one; empty → derived from the model name (piper-amy → "amy"), which the
# gateway requires as a field and then ignores.
TTS_VOICE      = os.environ.get("MOXIE_TTS_VOICE", "")
# The gateway's TTS model. Only read when MOXIE_VOICE_BASE_URL is set; "piper-amy" is
# the voice Moxie ships with (docs/guides/litellm-tts-setup.md).
VOICE_MODEL    = os.environ.get("MOXIE_VOICE_MODEL", "") or "piper-amy"
# "wav" (default) — the header carries the true sample rate, so a voice swap needs no
# config change. "pcm" — raw 16-bit frames at MOXIE_VOICE_SAMPLE_RATE (nothing in the
# payload can say otherwise). mp3/opus are NOT decoded here.
VOICE_FORMAT   = (os.environ.get("MOXIE_VOICE_FORMAT", "").strip().lower() or "wav")



def _env_int(name, default):
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return int(default)


# Sample rate of a raw-PCM reply — pcm ONLY (a wav reply carries its own). 22050 is what
# the gateway's Piper voices render at.
VOICE_SAMPLE_RATE = _env_int("MOXIE_VOICE_SAMPLE_RATE", 22050)
# Local Piper voice (offline, our default/primary — Amy). Path to a Piper .onnx model;
# when set + piper installed, used if no voice server is configured. Empty → off.
PIPER_MODEL    = os.environ.get("MOXIE_PIPER_MODEL", "")
PIPER_CONFIG   = os.environ.get("MOXIE_PIPER_CONFIG", "")
# Voice engine hint. "" = auto (voice server / piper / none). "tone" = the built-in
# zero-dep placeholder voice (demos/CI/SIL audio round-trip). "off" = force no voice.
TTS_ENGINE     = os.environ.get("MOXIE_TTS", "").lower()

# --- STT (AI seam §1): the ears. Two FIRST-CLASS engines, one env line apart ---------
# "auto"    (default) the gateway when one is configured (an STT base URL resolves, a key
#           is present and the openai SDK is importable), else local faster-whisper when
#           it is installed, else off. A hosted deployment therefore hears out of the box;
#           a keyless box behaves exactly as it did before this knob existed.
# "gateway" force the cloud ears (with local whisper as their standby, see below).
# "whisper" / "local"  force LOCAL faster-whisper **even when a gateway URL is set** —
#           a home appliance that keeps a child's voice inside the house is a supported
#           deployment, not a degraded one (the same statement `MOXIE_TTS=piper` makes
#           for the voice).
# "off"     no ears at all; text turns still work.
STT_ENABLED = os.environ.get("MOXIE_STT", "auto").strip().lower()
# Unset → the selected engine's own default (below); set → passed to whichever engine
# runs, so name a model that engine knows.
STT_MODEL   = os.environ.get("MOXIE_STT_MODEL", "").strip()
#: What the gateway calls its ears (`graphling-stt` and `stt-whisper-base` also exist).
GATEWAY_STT_MODEL = "stt-whisper"
#: faster-whisper's smallest English model — the local default since M3.
LOCAL_STT_MODEL = "base.en"
# One gateway, one key: the STT endpoint defaults to the voice endpoint and then to the
# brain's, because on our LiteLLM proxy they are the same host with the same key. Set
# MOXIE_STT_BASE_URL only to point the ears somewhere else.
STT_BASE_URL = (os.environ.get("MOXIE_STT_BASE_URL", "").strip()
                or VOICE_BASE_URL or LLM_BASE_URL)
STT_API_KEY  = (os.environ.get("MOXIE_STT_API_KEY", "").strip()
                or VOICE_API_KEY or LLM_API_KEY)

# --- brain latency (background inference + filler) ---
# Seconds a turn's brain call may run before the runtime speaks a short filler line
# (RemoteChatResponse result=REPLY_PENDING, chunk 0) and delivers the real answer as
# chunk 1. The robot re-prompts after ~20 s of cloud silence, and a live gateway turn
# was measured at 45 s, so this is what keeps a child from hearing nothing. 0 = off
# (one SUCCESS reply, whenever it lands).
def _env_float(name, default):
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return float(default)


BRAIN_BUDGET_S = _env_float("MOXIE_BRAIN_BUDGET_S", 6.0)

# --- streaming replies (a sentence at a time) ---
# When the app can answer incrementally (MoxieApp.respond_stream), publish each finished
# sentence as its own RemoteChatResponse chunk (result=REPLY_PENDING + chunk_num, closed
# by consistency_control.is_completed) instead of waiting for the whole completion. The
# child hears the first sentence at first-token latency (~3-5 s) instead of at
# whole-answer latency (18-45 s). "0"/"off" → the old single-reply path.
STREAMING = (os.environ.get("MOXIE_STREAMING", "1").strip().lower()
             not in ("0", "off", "false", "no", ""))

# Webhook app (external avatar bridge)
WEBHOOK_ENDPOINT = os.environ.get("MOXIE_WEBHOOK_ENDPOINT", "")

# --- default child profile (until wired to the parent-app server's record) ---
CHILD_NICKNAME = os.environ.get("MOXIE_CHILD_NICKNAME", "friend")


def build_app():
    """Instantiate the configured MoxieApp."""
    import sys, os as _os
    sys.path.insert(0, _os.path.dirname(__file__))
    from moxie_sdk.apps import EchoApp, LLMApp, WebhookApp
    if MOXIE_APP == "echo":
        return EchoApp()
    if MOXIE_APP == "webhook":
        if not WEBHOOK_ENDPOINT:
            raise SystemExit("MOXIE_APP=webhook requires MOXIE_WEBHOOK_ENDPOINT")
        return WebhookApp(WEBHOOK_ENDPOINT)
    if MOXIE_APP == "content":
        return build_content_app()
    return LLMApp(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, model=LLM_MODEL)


def build_content_app():
    """A ContentApp running the configured module through the AI seam."""
    import json
    from moxie_sdk.content import load_modules, ContentApp
    from moxie_sdk.chat import make_openai_chat
    from moxie_sdk.apps.llm_app import DEFAULT_PERSONA
    path = CONTENT_MODULE
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    with open(path) as fh:
        module = load_modules(json.load(fh))
    chat = make_openai_chat(LLM_BASE_URL, LLM_API_KEY, LLM_MODEL)
    return ContentApp(module, chat, persona=DEFAULT_PERSONA)


def build_synthesizer():
    """A server voice (moxie_sdk.tts.Synthesizer).

    Explicit `MOXIE_TTS=piper` (alias `local`) or `gateway` (alias `openai`) selects that
    engine outright and exits loudly if it cannot be built. Otherwise the auto precedence is
    unchanged — **voice server > Piper > tone**: a voice server if
    MOXIE_VOICE_BASE_URL is set; else a local Piper voice if MOXIE_PIPER_MODEL is set +
    piper installed; else the built-in tone with MOXIE_TTS=tone; else None (a real robot
    self-synthesizes; the SIM needs one of these for audio).

    What is new is the STANDBY: the gateway voice is wrapped in a `FallbackSynthesizer`
    whose second engine is exactly what the next rung down would have been (Piper if it
    is configured and installed, else the tone). The gateway is someone else's box; when
    it 500s past the SDK's backoff or answers with an error body, the turn downgrades to
    a working voice instead of handing a child silence. It is reported once, on the first
    failure — see moxie_sdk/tts.py::FallbackSynthesizer.
    """
    from moxie_sdk.tts import make_voice_synthesizer, make_piper_synthesizer
    if TTS_ENGINE == "off":
        return None
    piper = make_piper_synthesizer(PIPER_MODEL, PIPER_CONFIG or None)
    # Explicit engines win over the auto precedence (owner rule: local stays first-class,
    # one env line away even with a gateway fully configured — the mirror of MOXIE_STT).
    if TTS_ENGINE in ("piper", "local"):
        if piper is None:
            raise SystemExit("MOXIE_TTS=piper but no local Piper voice could be built — "
                             "set MOXIE_PIPER_MODEL to a voice .onnx and install piper-tts")
        return piper
    if TTS_ENGINE in ("gateway", "openai") and not VOICE_BASE_URL:
        raise SystemExit("MOXIE_TTS=gateway but MOXIE_VOICE_BASE_URL is not set")
    if VOICE_BASE_URL:
        from moxie_sdk.tts import FallbackSynthesizer, ToneSynthesizer
        voice = make_voice_synthesizer(VOICE_BASE_URL, VOICE_API_KEY, TTS_VOICE,
                                       model=VOICE_MODEL,
                                       response_format=VOICE_FORMAT,
                                       sample_rate=VOICE_SAMPLE_RATE)
        return FallbackSynthesizer(voice, piper or ToneSynthesizer())
    if piper:
        return piper
    if TTS_ENGINE == "tone":                 # built-in zero-dep voice (SIL/demo)
        from moxie_sdk.tts import ToneSynthesizer
        return ToneSynthesizer()
    return None


def build_transcriber():
    """The ears (moxie_sdk.stt.Transcriber), or None when nothing can hear.

    Neither engine is the "real" one. **Local faster-whisper** keeps a child's voice on
    the box and needs no key; **the gateway** (live 2026-09-02) needs no 140 MB model and
    is what a hosted deployment — the SIM on Cloudflare, a VPS, a slim container — can
    actually run. `MOXIE_STT` picks: `whisper`/`local` and `gateway` are explicit and
    win over everything, `off` disables, and `auto` prefers the gateway *only when one is
    genuinely configured* (URL + key + SDK) and otherwise uses local whisper.

    Why `auto` also demands a KEY: `STT_BASE_URL` falls back to `LLM_BASE_URL`, which has
    a default, so a URL alone is never evidence that anyone meant to use the cloud. With
    no key the gateway can only answer 401 — local whisper is the better ears, and an
    unset environment keeps behaving exactly as it did before this knob existed.

    The gateway is wrapped in a `FallbackTranscriber` whose standby is the rung it
    displaced: local whisper when installed, else a `NullTranscriber` that returns "".
    An outage then costs one reported downgrade instead of an exception on the turn's
    transcription path (mirrors `build_synthesizer`'s standby voice). The standby is
    built eagerly, so a box that must not load the whisper weights should simply not
    install faster-whisper — then a gateway outage means Moxie hears nothing until it
    returns, which the log says out loud.
    """
    from moxie_sdk.stt import (FallbackTranscriber, NullTranscriber, OpenAITranscriber,
                               WhisperTranscriber, make_openai_transcriber)
    if STT_ENABLED == "off":
        return None
    local_ok = WhisperTranscriber.available()
    if STT_ENABLED in ("whisper", "local"):      # local wins over any gateway URL
        if not local_ok:
            raise SystemExit("MOXIE_STT=%s needs faster-whisper: "
                             "pip install 'moxie-cloud-sdk[stt]'" % STT_ENABLED)
        return WhisperTranscriber(model=STT_MODEL or LOCAL_STT_MODEL)
    gateway_ok = OpenAITranscriber.available(STT_BASE_URL)
    if STT_ENABLED == "gateway" and not gateway_ok:
        # Explicitly asked for the cloud ears and they cannot be built. Say so loudly
        # rather than quietly hearing with something else than what was configured.
        raise SystemExit("MOXIE_STT=gateway needs the openai SDK "
                         "(pip install 'moxie-cloud-sdk[llm]') and an STT endpoint "
                         "(MOXIE_STT_BASE_URL / MOXIE_VOICE_BASE_URL / MOXIE_LLM_BASE_URL)")
    if gateway_ok and (STT_ENABLED == "gateway" or (STT_ENABLED == "auto"
                                                    and bool(STT_API_KEY))):
        primary = make_openai_transcriber(STT_BASE_URL, STT_API_KEY,
                                          model=STT_MODEL or GATEWAY_STT_MODEL)
        if primary is not None:
            # The standby always runs the LOCAL default model — STT_MODEL, when set, names
            # a model on whichever engine was selected, and that is the gateway here.
            standby = (WhisperTranscriber(model=LOCAL_STT_MODEL) if local_ok
                       else NullTranscriber())
            return FallbackTranscriber(primary, standby)
    if local_ok:
        return WhisperTranscriber(model=STT_MODEL or LOCAL_STT_MODEL)
    return None
