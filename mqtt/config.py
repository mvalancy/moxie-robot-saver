"""Runtime configuration for the Moxie robot-cloud supervisor.
All local-first; override via environment variables or a git-ignored `mqtt/.env`
(see .env.example — never commit real endpoints/keys).

**Nothing here defaults to anyone's deployment**: a variable naming a host comes from the
environment or is empty, and an app that needs one exits naming the variable
(guard: `sim/tests/test_no_deployment_defaults.py`).
"""
import re
import os

#: Falsy spellings, shared by every switch in this file.
_OFF = ("", "0", "off", "false", "no")

#: The dotenv loader's own switches, read from the ENVIRONMENT only (a file cannot carry
#: the flag that decides whether it is read). `MOXIE_SKIP_DOTENV=1` hides `mqtt/.env` so a
#: test that unsets a variable and reloads this module is not refilled from the file.
#: `MOXIE_DOTENV=/path` reads another file (tests; config kept outside the checkout).
_SKIP_DOTENV = "MOXIE_SKIP_DOTENV"
_DOTENV_PATH = "MOXIE_DOTENV"


def _truthy(name: str) -> bool:
    """An environment switch that is set to anything but a falsy spelling."""
    return os.environ.get(name, "").strip().lower() not in _OFF


def _dotenv_value(raw: str) -> str:
    """The value half of a dotenv line, with a trailing `# comment` removed
    (`.env.example` documents values inline, so a copied file must not yield comment text).

    A quoted value is taken verbatim; otherwise a comment starts at the first `#` preceded
    by whitespace, so `pass#word` survives.
    """
    v = raw.strip()
    if not v:
        return ""
    if v[0] == "#":                          # the whole value is a comment -> unset
        return ""
    if v[0] in "\"'":
        q = v[0]
        end = v.find(q, 1)
        return v[1:end] if end != -1 else v[1:]
    cut = re.search(r"\s#", v)
    return (v[:cut.start()] if cut else v).strip()


def _load_env(path=None):
    """Load KEY=VALUE lines from a dotenv file into the environment (no dependency).

    Returns the file used, or None. The environment wins (`setdefault`); `MOXIE_SKIP_DOTENV`
    beats both, even over an explicit `path`.
    """
    if _truthy(_SKIP_DOTENV):
        return None
    path = (path or os.environ.get(_DOTENV_PATH, "").strip()
            or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), _dotenv_value(v))
    except FileNotFoundError:
        return None
    return path


DOTENV_LOADED = _load_env()

# --- broker ---
MQTT_HOST = os.environ.get("MOXIE_MQTT_HOST", "127.0.0.1")   # supervisor→broker (loopback)
MQTT_PORT = int(os.environ.get("MOXIE_MQTT_PORT", "1883"))   # plain listener for the supervisor

# --- broker credential (security-broker-auth.md §2.2) ---
# The supervisor's fleet-wide MQTT identity (reads `$SYS/broker/log`, writes any device's
# subtree). Unset = anonymous on an open broker (dev broker, SIL harness).
MQTT_USERNAME = os.environ.get("MOXIE_MQTT_USER", "")
# A literal password wins; otherwise the file (minted 0600 by the compose one-shot, so the
# secret is not visible to `docker inspect`).
MQTT_PASSWORD = os.environ.get("MOXIE_MQTT_PASSWORD", "")
MQTT_PASSWORD_FILE = os.environ.get("MOXIE_MQTT_PASSWORD_FILE", "")


def broker_credentials():
    """`(username, password)` for the supervisor's MQTT client — `("", "")` when unset.

    Read at connect time (the compose `certs` one-shot may mint the secret after import).
    An unreadable password file degrades to anonymous with a log line.
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

# Loopback HTTP status endpoint; env-overridable so parallel SIL runs don't collide.
STATUS_PORT = int(os.environ.get("MOXIE_STATUS_PORT", "8930"))

# The host/IP the ROBOT uses to reach the broker (goes into the endpoint QR).
BROKER_PUBLIC_HOST = os.environ.get("MOXIE_BROKER_HOST", "192.168.1.9")
BROKER_PUBLIC_PORT = int(os.environ.get("MOXIE_BROKER_PORT", "8883"))

# --- which MoxieApp drives the robot ---
# "llm" (default), "echo", "webhook", or "content" — the closed positive list lives in
# `moxie_sdk/brains.py`, and `build_brain` refuses anything that is not in it.
MOXIE_APP = os.environ.get("MOXIE_APP", "llm")

#: The RAW `MOXIE_APP` (no default). The pin reads this one: unset must not pin `llm`
#: and lock an unconfigured box out of the per-child picker (`moxie_sdk/brains.py`).
BRAIN_ENV = os.environ.get("MOXIE_APP", "")

# Content app: a data-driven module (conversations/globals) run through the AI seam.
CONTENT_MODULE = os.environ.get("MOXIE_CONTENT_MODULE", "content_modules/starter.json")

# LLM brain — any OpenAI-compatible endpoint (LiteLLM, Ollama, vLLM, LM Studio, ...).
# No default on purpose: apps that need a brain say so (`require_llm_base_url`).
LLM_BASE_URL = os.environ.get("MOXIE_LLM_BASE_URL", "").strip()
LLM_API_KEY  = os.environ.get("MOXIE_LLM_API_KEY", os.environ.get("LITELLM_MASTER_KEY", ""))
LLM_MODEL    = os.environ.get("MOXIE_LLM_MODEL", "graphling-medium")

#: Vendor-neutral loopback examples for the "set one of these" message.
_BRAIN_EXAMPLES = ("http://127.0.0.1:11434/v1  (Ollama)",
                   "http://127.0.0.1:8000/v1   (vLLM / LM Studio / LiteLLM)")


def require_llm_base_url(app: str) -> str:
    """`LLM_BASE_URL`, or exit naming the missing variable.

    Fails at assembly (startup log), not on the first turn; like the webhook rule below,
    an app selected without what it needs is a misconfiguration, not a degraded mode.
    """
    if LLM_BASE_URL:
        return LLM_BASE_URL
    raise SystemExit(
        f"MOXIE_APP={app} needs MOXIE_LLM_BASE_URL — this repo ships no default brain "
        "endpoint on purpose (it is public, and a default would point every fork at one "
        "deployment). Set it to any OpenAI-compatible base URL, for example:\n"
        + "".join(f"    MOXIE_LLM_BASE_URL={e}\n" for e in _BRAIN_EXAMPLES)
        + "  (put it in mqtt/.env, or in .env for the compose stack — see .env.example.)\n"
        "  MOXIE_APP=echo needs no brain at all and is the way to bring the stack up "
        "without one.")

# AI voice server (optional) — server-side STT/TTS for the SIM + a server voice.
# OpenAI-compatible audio endpoints assumed (/audio/transcriptions, /audio/speech);
# key from MOXIE_VOICE_API_KEY (falls back to the LLM key). Empty → not configured.
VOICE_BASE_URL = os.environ.get("MOXIE_VOICE_BASE_URL", "")
VOICE_API_KEY  = os.environ.get("MOXIE_VOICE_API_KEY", LLM_API_KEY)
# Voice name for an OpenAI-shaped endpoint; on a LiteLLM gateway the MODEL is the voice,
# and empty derives it from the model name (piper-amy → "amy").
TTS_VOICE      = os.environ.get("MOXIE_TTS_VOICE", "")
# The gateway's TTS model (docs/guides/gateway-voice-and-ears.md).
VOICE_MODEL    = os.environ.get("MOXIE_VOICE_MODEL", "") or "piper-amy"
# "wav" (header carries the rate) or "pcm" (16-bit at MOXIE_VOICE_SAMPLE_RATE); no mp3/opus.
VOICE_FORMAT   = (os.environ.get("MOXIE_VOICE_FORMAT", "").strip().lower() or "wav")


def _env_num(cast, name, default):
    try:
        return cast(os.environ.get(name) or default)
    except ValueError:
        return cast(default)


def _env_int(name, default):
    return _env_num(int, name, default)


def _env_float(name, default):
    return _env_num(float, name, default)


# Sample rate of a raw-PCM reply (Piper renders 22050).
VOICE_SAMPLE_RATE = _env_int("MOXIE_VOICE_SAMPLE_RATE", 22050)
# Local Piper voice (.onnx path); used when no voice server is configured. Empty → off.
PIPER_MODEL    = os.environ.get("MOXIE_PIPER_MODEL", "")
PIPER_CONFIG   = os.environ.get("MOXIE_PIPER_CONFIG", "")
# Voice engine: "" auto (server / piper / none), "piper", "gateway", "tone" (zero-dep
# placeholder for SIL/CI), "off".
TTS_ENGINE     = os.environ.get("MOXIE_TTS", "").lower()
# Local Piper voices for the 🎚️ picker (read-only discovery; default git-ignored dir).
VOICES_DIR     = (os.environ.get("MOXIE_VOICES_DIR", "").strip()
                  or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "sim", "tts", "voices"))

# --- STT (AI seam §1): two first-class engines ---
# "auto" gateway when configured (URL + key + openai SDK), else local faster-whisper, else
# off · "gateway" force cloud (whisper standby) · "whisper"/"local" force local even with a
# gateway set (keeping a child's voice in the house is supported) · "off" text turns only.
STT_ENABLED = os.environ.get("MOXIE_STT", "auto").strip().lower()
# Passed to whichever engine runs; unset → that engine's default below.
STT_MODEL   = os.environ.get("MOXIE_STT_MODEL", "").strip()
#: What the gateway calls its ears (`graphling-stt` and `stt-whisper-base` also exist).
GATEWAY_STT_MODEL = "stt-whisper"
#: faster-whisper's smallest English model — the local default.
LOCAL_STT_MODEL = "base.en"
# STT endpoint/key default to the voice's, then the brain's (one gateway, one key).
STT_BASE_URL = (os.environ.get("MOXIE_STT_BASE_URL", "").strip()
                or VOICE_BASE_URL or LLM_BASE_URL)
STT_API_KEY  = (os.environ.get("MOXIE_STT_API_KEY", "").strip()
                or VOICE_API_KEY or LLM_API_KEY)

# --- the honest ears (AI seam §1 "What the ears refuse to hear") ---
# Read by `moxie_sdk/stt.py` itself (`ears_knobs`) each time a robot's listening session
# starts: the runtime never imports this module. Named here so every knob has one home.
# The levels come from the hosted page's browser microphones, not Moxie's: tune them on
# bench day from the console feed's "heard nothing" lines, which carry the numbers.
#: `off` (0/false/no) restores the ears exactly as they were: every clip to the engine,
#: its text verbatim, local whisper without its voice detector. Unset, empty or blank is
#: on: the same expression as `stt.ears_knobs()`, which a test pins value for value.
STT_PHANTOM_GATE = (((os.environ.get("MOXIE_STT_PHANTOM_GATE") or "").strip().lower()
                     or "on") not in _OFF)
#: RMS level (fraction of full scale) below which a clip is room tone: one of Whisper's
#: silence phrases ("Bye.", "Thank you.", "you") on it is not the child's word.
STT_ROOM_TONE_RMS = _env_float("MOXIE_STT_ROOM_TONE_RMS", 0.01)
#: Milliseconds below which a clip that is not loud is too short for that phrase to be real.
STT_MIN_SPEECH_MS = _env_float("MOXIE_STT_MIN_SPEECH_MS", 250)
# Seconds the 🎚️ picker trusts one `GET /v1/models` listing (refreshed off the turn path).
VOICE_DISCOVERY_TTL_S = _env_int("MOXIE_VOICE_DISCOVERY_TTL_S", 300)

# Seconds a turn's brain call may run before a filler line (REPLY_PENDING, chunk 0) is
# spoken; the robot re-prompts after ~20 s of cloud silence. 0 = off.
BRAIN_BUDGET_S = _env_float("MOXIE_BRAIN_BUDGET_S", 6.0)

# --- sandboxed content extensions (backlog/sandboxed-extensions.md §6.2) ---
# Budget for a pack's `extension` program (`moxie_sdk/content/ext/`). Chosen, not
# measured — hence env vars.
EXT_MAX_STEPS = _env_int("MOXIE_EXT_MAX_STEPS", 10000)
EXT_MAX_VALUE_BYTES = _env_int("MOXIE_EXT_MAX_VALUE_BYTES", 16384)
EXT_MAX_TOTAL_BYTES = _env_int("MOXIE_EXT_MAX_TOTAL_BYTES", 262144)
EXT_MAX_BREACHES = _env_int("MOXIE_EXT_MAX_BREACHES", 3)

# ---- ✍️ content authoring (backlog/content-authoring.md §5.2) ----
# The console's 💬 *Try it* (moxie_runtime/tryit.py reads MOXIE_AUTHOR_TRY_BUDGET per call,
# with this same default). Counts tries, not tokens — nothing here does token accounting, so
# this is not cost control. The max-tokens cap is for trying a DRAFT, which is not built yet.
AUTHOR_TRY_BUDGET = _env_int("MOXIE_AUTHOR_TRY_BUDGET", 40)        # tries per rolling hour
AUTHOR_TRY_MAX_TOKENS = _env_int("MOXIE_AUTHOR_TRY_MAX_TOKENS", 300)  # cap on a draft's own

#: Carved out of the turn, not added to it; a budget >= the turn's fails at startup.
EXT_BUDGET_S = _env_float("MOXIE_EXT_BUDGET_S", 0.25)

if EXT_BUDGET_S >= BRAIN_BUDGET_S:
    raise ValueError(
        f"MOXIE_EXT_BUDGET_S ({EXT_BUDGET_S}s) must be strictly less than "
        f"MOXIE_BRAIN_BUDGET_S ({BRAIN_BUDGET_S}s): an extension is a slice of the "
        f"turn, not a claim on it. Lower MOXIE_EXT_BUDGET_S or raise "
        f"MOXIE_BRAIN_BUDGET_S.")

# --- the durable store's cross-process lock (production-hardening.md §3.3) ---
#: How long a `JsonStore` write waits for another process's lock before refusing. Read by
#: `moxie_sdk/store.py` itself; guarded here like the extension budget, because some writes
#: run on the paho thread and must not outlast a turn. Chosen, not measured.
STORE_LOCK_TIMEOUT_S = _env_float("MOXIE_STORE_LOCK_TIMEOUT_S", 2.0)

if STORE_LOCK_TIMEOUT_S >= BRAIN_BUDGET_S:
    raise ValueError(
        f"MOXIE_STORE_LOCK_TIMEOUT_S ({STORE_LOCK_TIMEOUT_S}s) must be strictly less "
        f"than MOXIE_BRAIN_BUDGET_S ({BRAIN_BUDGET_S}s): waiting for another process's "
        f"store lock is a slice of the turn, not a claim on it. Lower "
        f"MOXIE_STORE_LOCK_TIMEOUT_S or raise MOXIE_BRAIN_BUDGET_S.")

# --- bounded engine calls (production-hardening.md §4.4 "gateway hangs", §9 A27) ---
# Seconds one request to the brain / ears / voice may take before it is an offline-class
# error instead of a hung worker. The openai SDK's own default is 600 s per request and
# the backoff retried it, so an endpoint that accepted connections and never answered
# held one turn worker for 5 x 600 s. Each knob is also the deadline past which no retry
# starts (`moxie_sdk.chat.call_with_backoff(deadline_s=...)`): a timeout is never
# retried, so a wedged gateway costs one bound; a fast 429/5xx is retried only while
# the retry starts inside the bound, and that retry runs its own request bound, so one
# call costs at most just under two. Hang bounds, chosen not measured: the brain's sits
# above the filler budget and a slow local model's whole non-streamed completion (it
# also caps the memory summary, the longest completion); the ears' inside the broker's
# keepalive drop (the transcript is produced on the broker thread, which an ears outage
# stalls for up to one bound per retry window). 0 or less is REFUSED at startup, below:
# it is not "no bound", it is the hang these knobs exist to end.
BRAIN_TIMEOUT_S = _env_float("MOXIE_BRAIN_TIMEOUT_S", 60.0)
STT_TIMEOUT_S = _env_float("MOXIE_STT_TIMEOUT_S", 12.0)
TTS_TIMEOUT_S = _env_float("MOXIE_TTS_TIMEOUT_S", 15.0)

for _knob, _seconds in (("MOXIE_BRAIN_TIMEOUT_S", BRAIN_TIMEOUT_S),
                        ("MOXIE_STT_TIMEOUT_S", STT_TIMEOUT_S),
                        ("MOXIE_TTS_TIMEOUT_S", TTS_TIMEOUT_S)):
    if not 0 < _seconds < float("inf"):           # 0, a negative, NaN and inf
        raise ValueError(
            f"{_knob} ({_seconds:g}s) must be a positive number of seconds: 0 is not "
            f"'no bound', it is the hang this knob exists to end. Unset it for the "
            f"default, or set the seconds a slow model really needs.")
del _knob, _seconds

# Seconds a standby engine (local whisper / Piper / the tone behind a gateway) keeps the
# turn before the next call tries the gateway again; an answer clears the latch. Before
# this knob the first failure latched the standby for the rest of the run, and with no
# local whisper installed that standby hears nothing. 0 = try the gateway on every call
# (a negative value counts as 0).
ENGINE_RETRY_S = _env_float("MOXIE_ENGINE_RETRY_S", 60.0)

# --- streaming replies ---
# Publish each finished sentence as its own REPLY_PENDING chunk (first sentence at
# first-token latency). "0"/"off" → one reply.
STREAMING = os.environ.get("MOXIE_STREAMING", "1").strip().lower() not in _OFF

# Webhook app (external avatar bridge)
WEBHOOK_ENDPOINT = os.environ.get("MOXIE_WEBHOOK_ENDPOINT", "")

# --- default child profile (until wired to the parent-app server's record) ---
CHILD_NICKNAME = os.environ.get("MOXIE_CHILD_NICKNAME", "friend")


def _sdk_path():
    """Put this directory on `sys.path` so `moxie_sdk` imports. Called by the builders
    rather than at module import, which is why nothing above imports the SDK."""
    import sys
    sys.path.insert(0, os.path.dirname(__file__))


def _build_echo():
    _sdk_path()
    from moxie_sdk.apps import EchoApp
    return EchoApp()


def _build_webhook():
    _sdk_path()
    from moxie_sdk.apps import WebhookApp
    if not WEBHOOK_ENDPOINT:
        raise SystemExit("MOXIE_APP=webhook requires MOXIE_WEBHOOK_ENDPOINT")
    return WebhookApp(WEBHOOK_ENDPOINT)


def _build_llm():
    _sdk_path()
    from moxie_sdk.apps import LLMApp
    return LLMApp(base_url=require_llm_base_url("llm"), api_key=LLM_API_KEY,
                  model=LLM_MODEL, timeout_s=BRAIN_TIMEOUT_S)


#: `{brain id: builder}` — the other half of `moxie_sdk.brains.BRAINS` (a test pins the
#: two tables to the same keys).
BRAIN_BUILDERS = {
    "llm": _build_llm,
    "content": lambda: build_content_app(),
    "webhook": _build_webhook,
    "echo": _build_echo,
}


def _unknown_brain(name) -> SystemExit:
    """The one refusal for a `MOXIE_APP` nobody can build, shared by every path."""
    _sdk_path()
    from moxie_sdk import brains
    return SystemExit(
        f"MOXIE_APP={str(name)!r} is not a brain this appliance knows. "
        f"Choose one of: {brains.offered()} — or MOXIE_APP=any to leave the choice "
        f"to the console, per child (docs/architecture/ai-seam.md §2).")


def default_brain() -> str:
    """The brain the `defaults` layer contributes.

    `MOXIE_APP` when it names one; `brains.DEFAULT_BRAIN` for unset/`any`/`auto`. Anything
    else raises — a typo must not silently become the default brain.
    """
    _sdk_path()
    from moxie_sdk import brains
    name = brains.sanitize_brain(MOXIE_APP)
    if name:
        return name
    if str(MOXIE_APP or "").strip().lower() in brains.NO_PIN_VALUES:
        return brains.DEFAULT_BRAIN
    raise _unknown_brain(MOXIE_APP)


def brain_pin() -> str:
    """Which brain `MOXIE_APP` pins — `""` when none. The single reader, so builders and
    the console card cannot disagree (as `engine_pins()` for the voice)."""
    _sdk_path()
    from moxie_sdk import brains
    return brains.pin_for_env(BRAIN_ENV)


def build_brain(name):
    """Instantiate ONE brain by name; a name not in `brains.BRAINS` exits naming what is
    offered."""
    _sdk_path()
    from moxie_sdk import brains
    key = brains.sanitize_brain(name)
    if not key or key not in BRAIN_BUILDERS:
        raise _unknown_brain(name)
    return BRAIN_BUILDERS[key]()


def build_app():
    """The appliance's own MoxieApp — the `defaults` layer, from `MOXIE_APP`. Fleet and
    per-robot layers sit above it (`brains.resolve_brain`, `MoxieRuntime.app_for`)."""
    return build_brain(default_brain())


def build_content_app():
    """A ContentApp running the configured module through the AI seam.

    Effective content = the shipped file, then the imported overlay by `kind:key`
    (backlog/content-packs.md §2.4). Both are kept on the app (`content_defaults`, `module`)
    so reload/undo can rebuild without a restart.
    """
    base_url = require_llm_base_url("content")   # a content module still answers via the
                                                 # AI seam, so it needs a brain endpoint
    import json
    from moxie_sdk.content import ContentApp, packs
    from moxie_sdk.chat import make_openai_chat
    from moxie_sdk.store import JsonStore
    from moxie_sdk.apps.llm_app import DEFAULT_PERSONA
    path = CONTENT_MODULE
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    with open(path) as fh:
        defaults = packs.shipped_items(json.load(fh))
    stored = JsonStore().read_shared("content_items", {}) or {}
    overlay = stored.get("items") if isinstance(stored, dict) else None
    overlay = overlay if isinstance(overlay, dict) else {}
    module = packs.build_module(defaults, overlay)
    if overlay:
        print(f"[config] 📦 content: {len(defaults)} shipped + {len(overlay)} imported")
    chat = make_openai_chat(base_url, LLM_API_KEY, LLM_MODEL, timeout_s=BRAIN_TIMEOUT_S)
    return ContentApp(module, chat, persona=DEFAULT_PERSONA, content_defaults=defaults)


def _gateway_voice(model, piper):
    """The gateway voice with the next rung (Piper, else tone) as its standby, so an
    outage downgrades the voice instead of going silent. None when it cannot be built."""
    from moxie_sdk.tts import FallbackSynthesizer, ToneSynthesizer, make_voice_synthesizer
    voice = make_voice_synthesizer(VOICE_BASE_URL, VOICE_API_KEY, TTS_VOICE, model=model,
                                   response_format=VOICE_FORMAT,
                                   sample_rate=VOICE_SAMPLE_RATE,
                                   timeout_s=TTS_TIMEOUT_S)
    return None if voice is None else FallbackSynthesizer(voice, piper or ToneSynthesizer(),
                                                          retry_s=ENGINE_RETRY_S)


def _speech_for_choice(choice, piper):
    """The engine one 🎚️ speech choice names, or None when it cannot be built here.
    `piper` (or None) is reused as the gateway's standby."""
    from moxie_sdk import voice_settings
    from moxie_sdk.tts import ToneSynthesizer, make_piper_synthesizer
    engine, model = choice["engine"], choice["model"]
    if engine == "tone":
        return ToneSynthesizer()
    if engine == "piper":
        # A picked LOCAL voice wins even with a gateway fully configured (owner rule).
        path = voice_settings.piper_voice_path(model, PIPER_MODEL, VOICES_DIR)
        if not path:
            return None
        cfg = PIPER_CONFIG if (PIPER_CONFIG and path == PIPER_MODEL) else None
        return make_piper_synthesizer(path, cfg or None)
    if engine == "gateway" and VOICE_BASE_URL:
        return _gateway_voice(model, piper)
    return None


def build_synthesizer(override=None):
    """A server voice (moxie_sdk.tts.Synthesizer), or None.

    `override` is the 🎚️ console pick (`{"engine", "model"}`, `moxie_sdk/voice_settings.py`).
    Precedence:
      * `MOXIE_TTS=off` wins outright.
      * An explicit `MOXIE_TTS` pins the engine: a pick naming another engine is ignored,
        a pick within it (which Piper voice) applies.
      * A pick that cannot be built here falls through to the env path.
      * Explicit `piper`/`local` or `gateway`/`openai` exits loudly if unbuildable.
      * Auto: voice server > Piper > tone (only with `MOXIE_TTS=tone`) > None (a real robot
        self-synthesizes).
    The gateway voice is wrapped in a `FallbackSynthesizer` whose standby is the next rung
    (Piper, else tone), so an outage downgrades the voice instead of going silent.
    """
    from moxie_sdk import voice_settings
    from moxie_sdk.tts import ToneSynthesizer, make_piper_synthesizer
    if TTS_ENGINE == "off":
        return None
    piper = make_piper_synthesizer(PIPER_MODEL, PIPER_CONFIG or None)
    choice = voice_settings.sanitize_choice(voice_settings.SPEECH, override)
    if not voice_settings.honours_pin(voice_settings.SPEECH, choice,
                                      voice_settings.pin_for_env(voice_settings.SPEECH,
                                                                 TTS_ENGINE)):
        choice = None                        # the operator's MOXIE_TTS names the engine
    if choice:
        picked = _speech_for_choice(choice, piper)
        if picked is not None:
            return picked
    # Explicit engines win over auto (local stays first-class even with a gateway set).
    if TTS_ENGINE in ("piper", "local"):
        if piper is None:
            raise SystemExit("MOXIE_TTS=piper but no local Piper voice could be built — "
                             "set MOXIE_PIPER_MODEL to a voice .onnx and install piper-tts")
        return piper
    if TTS_ENGINE in ("gateway", "openai") and not VOICE_BASE_URL:
        raise SystemExit("MOXIE_TTS=gateway but MOXIE_VOICE_BASE_URL is not set")
    if VOICE_BASE_URL:
        return _gateway_voice(VOICE_MODEL, piper)
    if piper:
        return piper
    if TTS_ENGINE == "tone":                 # built-in zero-dep voice (SIL/demo)
        return ToneSynthesizer()
    return None


def _gateway_ears(model):
    """The gateway transcriber with local whisper (its default model — `model` names a
    gateway model) or a `NullTranscriber` as standby. None when it cannot be built."""
    from moxie_sdk.stt import (FallbackTranscriber, NullTranscriber, WhisperTranscriber,
                               make_openai_transcriber)
    primary = make_openai_transcriber(STT_BASE_URL, STT_API_KEY, model=model,
                                      timeout_s=STT_TIMEOUT_S)
    if primary is None:
        return None
    standby = (WhisperTranscriber(model=LOCAL_STT_MODEL)
               if WhisperTranscriber.available() else NullTranscriber())
    return FallbackTranscriber(primary, standby, retry_s=ENGINE_RETRY_S)


def _listening_for_choice(choice):
    """The ears one 🎚️ listening choice names, or None when they cannot be built here.
    `off` is handled by the caller (it is also None, with a different meaning)."""
    from moxie_sdk.stt import WhisperTranscriber
    engine, model = choice["engine"], choice["model"]
    if engine == "whisper":
        # A picked LOCAL engine wins even with a gateway fully configured (owner rule).
        if not WhisperTranscriber.available():
            return None
        return WhisperTranscriber(model=model or LOCAL_STT_MODEL)
    if engine == "gateway":
        return _gateway_ears(model or GATEWAY_STT_MODEL)
    return None


def build_transcriber(override=None):
    """The ears (moxie_sdk.stt.Transcriber), or None when nothing can hear.

    `override` (🎚️ console pick) follows `build_synthesizer`'s precedence: under an explicit
    `MOXIE_STT` pin, above `auto`, falling through when unbuildable. Local faster-whisper
    keeps a child's voice on the box; the gateway needs no model and suits hosted
    deployments. `auto` picks the gateway only with URL + key + SDK (a URL alone may just be
    the brain's), else local whisper. The gateway is wrapped in a `FallbackTranscriber`
    whose standby is local whisper or a `NullTranscriber`.
    """
    from moxie_sdk import voice_settings
    from moxie_sdk.stt import OpenAITranscriber, WhisperTranscriber
    if STT_ENABLED == "off":
        return None
    choice = voice_settings.sanitize_choice(voice_settings.LISTENING, override)
    if not voice_settings.honours_pin(voice_settings.LISTENING, choice,
                                      voice_settings.pin_for_env(voice_settings.LISTENING,
                                                                 STT_ENABLED)):
        choice = None                        # the operator's MOXIE_STT names the engine
    if choice:
        if choice["engine"] == "off":
            return None
        picked = _listening_for_choice(choice)
        if picked is not None:
            return picked
    local_ok = WhisperTranscriber.available()
    if STT_ENABLED in ("whisper", "local"):      # local wins over any gateway URL
        if not local_ok:
            raise SystemExit("MOXIE_STT=%s needs faster-whisper: "
                             "pip install 'moxie-cloud-sdk[stt]'" % STT_ENABLED)
        return WhisperTranscriber(model=STT_MODEL or LOCAL_STT_MODEL)
    gateway_ok = OpenAITranscriber.available(STT_BASE_URL)
    if STT_ENABLED == "gateway" and not gateway_ok:
        raise SystemExit("MOXIE_STT=gateway needs the openai SDK "
                         "(pip install 'moxie-cloud-sdk[llm]') and an STT endpoint "
                         "(MOXIE_STT_BASE_URL / MOXIE_VOICE_BASE_URL / MOXIE_LLM_BASE_URL)")
    if gateway_ok and (STT_ENABLED == "gateway" or (STT_ENABLED == "auto" and STT_API_KEY)):
        ears = _gateway_ears(STT_MODEL or GATEWAY_STT_MODEL)
        if ears is not None:
            return ears
    if local_ok:
        return WhisperTranscriber(model=STT_MODEL or LOCAL_STT_MODEL)
    return None


# --- 🎚️ the voice picker (backlog/voice-picker.md) ---
# The runtime holds a `VoiceEngines` and never imports `config`, so tests can pass a fake.

def gateway_model_ids():
    """Every model id the voice gateway lists (one `GET /models`); voice vs ears is decided
    by name in `moxie_sdk/audio_models.py`. Bounded by the voice knob: the listing is the
    voice gateway's, refreshed on a background thread that must not hang for 600 s."""
    from openai import OpenAI                 # lazy — the SDK is an optional extra
    from moxie_sdk.chat import client_timeout
    client = OpenAI(base_url=VOICE_BASE_URL, api_key=VOICE_API_KEY or "sk-local",
                    max_retries=0, timeout=client_timeout(TTS_TIMEOUT_S))
    return [getattr(m, "id", "") for m in (client.models.list().data or [])]


def local_piper_voices():
    """Local Piper voices this box can speak with — needs both the package and an `.onnx`."""
    from moxie_sdk import voice_settings
    from moxie_sdk.tts import PiperSynthesizer
    if not PiperSynthesizer.available():
        return []
    return voice_settings.piper_voices(PIPER_MODEL, VOICES_DIR)


def local_whisper_models():
    """Local whisper sizes to offer: the default and `MOXIE_STT_MODEL` only (any other size
    would download on first use). `[]` without faster-whisper."""
    from moxie_sdk.stt import WhisperTranscriber
    if not WhisperTranscriber.available():
        return []
    names = [LOCAL_STT_MODEL]
    if STT_MODEL and STT_MODEL not in names:
        names.append(STT_MODEL)
    return names


def engine_pins() -> dict:
    """Which engine `MOXIE_TTS`/`MOXIE_STT` pin — `""` where none (the single reader)."""
    from moxie_sdk import voice_settings
    return {voice_settings.SPEECH:
            voice_settings.pin_for_env(voice_settings.SPEECH, TTS_ENGINE),
            voice_settings.LISTENING:
            voice_settings.pin_for_env(voice_settings.LISTENING, STT_ENABLED)}


class VoiceEngines:
    """The runtime's seam onto this module for the 🎚️ picker. `available()` never blocks:
    the gateway listing is cached and refreshed in the background (`discovering: True`)."""

    def __init__(self, catalog=None):
        from moxie_sdk import voice_settings
        self.catalog = catalog if catalog is not None else voice_settings.GatewayCatalog(
            gateway_model_ids if VOICE_BASE_URL else None,
            ttl_s=VOICE_DISCOVERY_TTL_S)

    def available(self, *, refresh: bool = False, settle_s: float = 0.0) -> dict:
        """`{available: {speech, listening}, pins, pin_notes, discovering, gateway_error}`.

        `settle_s`: bounded wait a console write may ask for on a cold catalog. The list is
        already filtered by the env pins, so the dropdown never offers what builders refuse.
        """
        from moxie_sdk import voice_settings
        snap = self.catalog.snapshot(refresh=refresh, settle_s=settle_s)
        pins = engine_pins()
        return {"available": voice_settings.filter_available(
                    voice_settings.build_available(
                        snap["ids"], piper_voices=local_piper_voices(),
                        whisper_models=local_whisper_models()),
                    pins),
                "pins": pins,
                "pin_notes": {voice_settings.SPEECH:
                              voice_settings.pin_note(voice_settings.SPEECH, TTS_ENGINE),
                              voice_settings.LISTENING:
                              voice_settings.pin_note(voice_settings.LISTENING,
                                                      STT_ENABLED)},
                "discovering": snap["discovering"],
                "gateway_error": snap["gateway_error"]}

    def build_speech(self, choice):
        return build_synthesizer(override=choice)

    def build_listening(self, choice):
        return build_transcriber(override=choice)


def voice_engines(catalog=None) -> "VoiceEngines":
    """The appliance's `VoiceEngines` — what `run.py` hands the runtime at boot."""
    return VoiceEngines(catalog)


# --- 🧠 the brain picker: the mirror of `VoiceEngines` for seam ② (ai-seam.md) ---

class BrainEngines:
    """What this appliance can think with, and how to build any of it (a static table —
    no discovery)."""

    def available(self) -> dict:
        """`{available, pin, pin_note, default}`, already filtered by the `MOXIE_APP` pin."""
        _sdk_path()
        from moxie_sdk import brains
        pin = brain_pin()
        return {"available": brains.filter_options(
                    brains.options(default=default_brain()), pin),
                "pin": pin,
                "pin_note": brains.pin_note(BRAIN_ENV),
                "default": default_brain()}

    def build(self, name):
        return build_brain(name)


def brain_engines() -> "BrainEngines":
    """The appliance's `BrainEngines` — what `run.py` hands the runtime at boot."""
    return BrainEngines()
