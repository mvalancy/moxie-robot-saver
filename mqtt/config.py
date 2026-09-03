"""Runtime configuration for the Moxie robot-cloud supervisor.
All local-first; override via environment variables or a git-ignored `mqtt/.env`
(see .env.example — never commit real endpoints/keys).

**Nothing here defaults to anyone's deployment.** This repo is public and the stated
principle is that any Moxie sim and any OpenAI-compatible gateway work by configuration,
so a variable that names a *host* either comes from the environment or is empty — and an
app that cannot run without one exits saying which variable to set. The hosted Functions
already work this way (`functions/api/_lib/env.js`: `DEMO_GATEWAY_BASE_URL` has no
default, and unset means degraded, never "assume ours" — `backlog/live-sim-demo.md` C3).
`sim/tests/test_no_deployment_defaults.py` is the guard that keeps it true.
"""
import re
import os

#: Falsy spellings, shared by every switch in this file.
_OFF = ("", "0", "off", "false", "no")

#: The two switches for the dotenv loader itself. They are read from the ENVIRONMENT and
#: nowhere else, because a file cannot carry the flag that decides whether it is read.
#:
#: `MOXIE_SKIP_DOTENV=1` makes a present `mqtt/.env` invisible. It exists because the file
#: is loaded with `setdefault` at import, which is exactly right for an appliance and
#: exactly wrong for a test: a suite that simulates "nothing is configured" by deleting a
#: variable and reloading this module had it **refilled from the file**, so on any machine
#: that has a real `mqtt/.env` those tests asserted nothing. `.env` is git-ignored, so CI
#: and every git worktree never saw it and the whole class was invisible (orchestration
#: playbook rule 20). The flag is the smallest thing that makes "unset" mean unset.
#:
#: `MOXIE_DOTENV=/path/to/file` reads that file instead of `mqtt/.env`. An injectable path
#: alone could not have fixed the above — `importlib.reload(config)` calls `_load_env()`
#: with no arguments — but it is what lets the loader be tested against a real dotenv file
#: without going near a developer's own `mqtt/.env`, and it lets a deployment keep its
#: configuration outside the checkout.
_SKIP_DOTENV = "MOXIE_SKIP_DOTENV"
_DOTENV_PATH = "MOXIE_DOTENV"


def _truthy(name: str) -> bool:
    """An environment switch that is set to anything but a falsy spelling."""
    return os.environ.get(name, "").strip().lower() not in _OFF


def _dotenv_value(raw: str) -> str:
    """The value half of a dotenv line, with a trailing `# comment` removed.

    Our own `mqtt/.env.example` documents values with inline comments, e.g.

        MOXIE_VOICE_BASE_URL=         # e.g. https://your-gateway/v1 (empty -> Piper/tone)

    and the documented first step is to copy that file. Without this, the value became the
    **comment text** — truthy garbage that `build_synthesizer` would then treat as a
    gateway URL, and `MOXIE_APP` became `"llm            # llm | content | echo"`. So the
    documented setup path produced a broken appliance. Found by the class guard added with
    the gateway-default fix.

    Rules, deliberately conservative:
      * a quoted value is taken verbatim inside the quotes, so a `#` may appear in it;
      * otherwise a comment starts at the first `#` **preceded by whitespace**, so a value
        like `pass#word` survives — only ` #` reads as a comment, which is the convention
        every dotenv file in this repo already follows.
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

    Returns the file it used, or None when it loaded nothing. The existing environment
    always wins (`setdefault`), so an explicit variable beats the file — and
    `MOXIE_SKIP_DOTENV` beats both, including an explicitly passed `path`, because the
    whole point of the flag is "this process must see no file at all".
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

# LLM brain — any OpenAI-compatible endpoint (LiteLLM, Ollama, vLLM, LM Studio, a hosted
# proxy). **There is no default, on purpose.** This file used to ship the maintainer's own
# gateway as the fallback, which meant a stranger who cloned a public repo got a
# supervisor silently pointed at someone else's server — and it never even worked, since
# that endpoint refuses unauthenticated calls, so the child heard "my brain got fuzzy"
# forever with no line anywhere saying why. Empty is the honest state, and the apps that
# need a brain say so out loud (`require_llm_base_url`).
LLM_BASE_URL = os.environ.get("MOXIE_LLM_BASE_URL", "").strip()
LLM_API_KEY  = os.environ.get("MOXIE_LLM_API_KEY", os.environ.get("LITELLM_MASTER_KEY", ""))
LLM_MODEL    = os.environ.get("MOXIE_LLM_MODEL", "graphling-medium")

#: Endpoints named in the "set one of these" message. Generic, runnable and vendor-neutral
#: — a local Ollama and a local vLLM, both on loopback. Nothing here names a deployment.
_BRAIN_EXAMPLES = ("http://127.0.0.1:11434/v1  (Ollama)",
                   "http://127.0.0.1:8000/v1   (vLLM / LM Studio / LiteLLM)")


def require_llm_base_url(app: str) -> str:
    """`LLM_BASE_URL`, or exit naming the variable that is missing.

    Called by every app that cannot answer a child without a brain. It fails at
    ASSEMBLY — `build_app()`, before the broker connection — rather than on the first
    turn, so the operator reads it in the startup log instead of discovering it as a
    fuzzy-brain reply hours later. The message names `MOXIE_LLM_BASE_URL` literally,
    because the previous behaviour's whole failing was that nothing was ever named.

    Mirrors the `MOXIE_APP=webhook requires MOXIE_WEBHOOK_ENDPOINT` rule below: an app
    selected without the one thing it needs is a misconfiguration, not a degraded mode.
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
# Where local Piper voices live, for the console's 🎚️ Voice picker. The repo's own
# `sim/tts/voices/` by default (git-ignored — 63 MB per voice), overridable for a box that
# keeps its models elsewhere. Read-only discovery: nothing here downloads anything.
VOICES_DIR     = (os.environ.get("MOXIE_VOICES_DIR", "").strip()
                  or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "sim", "tts", "voices"))

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
# How long the 🎚️ picker trusts one `GET /v1/models` listing before refreshing it in the
# background (seconds). Never on a turn's path — see moxie_sdk/voice_settings.py.
VOICE_DISCOVERY_TTL_S = _env_int("MOXIE_VOICE_DISCOVERY_TTL_S", 300)

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

# --- sandboxed content extensions (BEYOND #6, docs/architecture/backlog/
#     sandboxed-extensions.md §6.2) ---
# An `extension` is a small, total, capability-scoped program a content pack may carry
# (`moxie_sdk/content/ext.py`). These are its budget. Every default is chosen rather than
# measured (the brief's assumption A7 is explicit about that), which is exactly why each
# one is an env var: a week of `ext_events` on a real appliance is what settles them.
EXT_MAX_STEPS = _env_int("MOXIE_EXT_MAX_STEPS", 10000)
EXT_MAX_VALUE_BYTES = _env_int("MOXIE_EXT_MAX_VALUE_BYTES", 16384)
EXT_MAX_TOTAL_BYTES = _env_int("MOXIE_EXT_MAX_TOTAL_BYTES", 262144)
EXT_MAX_BREACHES = _env_int("MOXIE_EXT_MAX_BREACHES", 3)

#: **Carved out of the turn, not added to it.** An extension gets a slice of a child's
#: patience, not a claim on it: 0.25 s is 4 % of `BRAIN_BUDGET_S`, and if both the
#: `global` and the `turn.before` hook run, 8 %. The assertion below is the honest part of
#: that deal — a deployment that sets the extension budget above the turn budget has
#: written a configuration in which an extension can eat the whole turn, and it fails at
#: startup with a sentence rather than at 3 a.m. with a silent robot.
EXT_BUDGET_S = _env_float("MOXIE_EXT_BUDGET_S", 0.25)

if EXT_BUDGET_S >= BRAIN_BUDGET_S:
    raise ValueError(
        f"MOXIE_EXT_BUDGET_S ({EXT_BUDGET_S}s) must be strictly less than "
        f"MOXIE_BRAIN_BUDGET_S ({BRAIN_BUDGET_S}s): an extension is a slice of the "
        f"turn, not a claim on it. Lower MOXIE_EXT_BUDGET_S or raise "
        f"MOXIE_BRAIN_BUDGET_S.")

# --- streaming replies (a sentence at a time) ---
# When the app can answer incrementally (MoxieApp.respond_stream), publish each finished
# sentence as its own RemoteChatResponse chunk (result=REPLY_PENDING + chunk_num, closed
# by consistency_control.is_completed) instead of waiting for the whole completion. The
# child hears the first sentence at first-token latency (~3-5 s) instead of at
# whole-answer latency (18-45 s). "0"/"off" → the old single-reply path.
STREAMING = os.environ.get("MOXIE_STREAMING", "1").strip().lower() not in _OFF

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
    return LLMApp(base_url=require_llm_base_url("llm"), api_key=LLM_API_KEY,
                  model=LLM_MODEL)


def build_content_app():
    """A ContentApp running the configured module through the AI seam.

    **Effective content = the shipped file, then the imported overlay by `kind:key`**
    (📦 content packs, `docs/architecture/backlog/content-packs.md` §2.4). The two are kept
    apart on the app — `content_defaults` is the shipped baseline, `module` is the merge —
    because `MoxieRuntime.reload_content()` and `content_undo()` both need to rebuild one
    from the other without a restart.

    A fresh appliance has an empty overlay and therefore loads exactly what it always did.
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
    chat = make_openai_chat(base_url, LLM_API_KEY, LLM_MODEL)
    return ContentApp(module, chat, persona=DEFAULT_PERSONA, content_defaults=defaults)


def _speech_for_choice(choice, piper):
    """The engine one 🎚️ speech choice names, or None when it cannot be built here.

    `piper` is the already-built local voice (or None), reused as the gateway's standby so
    a picked cloud voice degrades exactly the way the env-driven one does.
    """
    from moxie_sdk import voice_settings
    from moxie_sdk.tts import (FallbackSynthesizer, ToneSynthesizer,
                               make_piper_synthesizer, make_voice_synthesizer)
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
    if engine == "gateway":
        if not VOICE_BASE_URL:
            return None
        voice = make_voice_synthesizer(VOICE_BASE_URL, VOICE_API_KEY, TTS_VOICE,
                                       model=model, response_format=VOICE_FORMAT,
                                       sample_rate=VOICE_SAMPLE_RATE)
        if voice is None:
            return None
        return FallbackSynthesizer(voice, piper or ToneSynthesizer())
    return None


def build_synthesizer(override=None):
    """A server voice (moxie_sdk.tts.Synthesizer).

    `override` is the 🎚️ console pick (`{"engine", "model"}` — see
    `moxie_sdk/voice_settings.py`), read from `fleet/voice.json` at boot and passed again
    on every live swap. It sits above the AUTO precedence, and under an explicit one:

      * **An explicit `MOXIE_TTS` pins the engine.** `piper`/`local`, `gateway`/`openai`,
        `tone` and `off` are an operator's statement about this deployment, and a pick
        naming a *different* engine is ignored (`voice_settings.honours_pin`). A pick
        *within* the pinned engine still applies — `MOXIE_TTS=piper` chooses local Piper,
        the console still chooses which installed voice. The console does not have to
        guess this: `VoiceEngines.available()` offers only the pinned engine's entries and
        carries `pin_note` to say why.
      * **`MOXIE_TTS=off` still wins outright** — a deployment that declared itself
        voiceless is not talked out of it by a dropdown.
      * **A pick that cannot be built here** — a gateway voice with no
        `MOXIE_VOICE_BASE_URL`, a Piper voice whose `.onnx` is gone — falls through to the
        env path rather than leaving a child in silence.

    Unset `MOXIE_TTS` pins nothing, and an unset `override` keeps the env-driven behaviour
    byte-for-byte.

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
    from moxie_sdk import voice_settings
    from moxie_sdk.tts import make_voice_synthesizer, make_piper_synthesizer
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


def _listening_for_choice(choice):
    """The ears one 🎚️ listening choice names, or None when they cannot be built here.

    `off` is handled by the caller, because "no ears" and "could not build these ears" are
    the same `None` and must not be confused.
    """
    from moxie_sdk.stt import (FallbackTranscriber, NullTranscriber, WhisperTranscriber,
                               make_openai_transcriber)
    engine, model = choice["engine"], choice["model"]
    if engine == "whisper":
        # A picked LOCAL engine wins even with a gateway fully configured (owner rule).
        if not WhisperTranscriber.available():
            return None
        return WhisperTranscriber(model=model or LOCAL_STT_MODEL)
    if engine == "gateway":
        primary = make_openai_transcriber(STT_BASE_URL, STT_API_KEY,
                                          model=model or GATEWAY_STT_MODEL)
        if primary is None:
            return None
        standby = (WhisperTranscriber(model=LOCAL_STT_MODEL)
                   if WhisperTranscriber.available() else NullTranscriber())
        return FallbackTranscriber(primary, standby)
    return None


def build_transcriber(override=None):
    """The ears (moxie_sdk.stt.Transcriber), or None when nothing can hear.

    `override` is the 🎚️ console pick, with the same precedence the voice has: above the
    `auto` path, **under an explicit `MOXIE_STT`, which pins the engine** (`whisper`/
    `local`, `gateway`, `off` — a pick naming another engine is ignored, a pick of another
    *model within* the pinned engine still applies), and falling through to the env path
    when the picked engine cannot be built on this box (see `build_synthesizer`). Unset —
    or `auto` — pins nothing, and an unset override keeps today's behaviour.

    Neither engine is the "real" one. **Local faster-whisper** keeps a child's voice on
    the box and needs no key; **the gateway** (live 2026-09-02) needs no 140 MB model and
    is what a hosted deployment — the SIM on Cloudflare, a VPS, a slim container — can
    actually run. `MOXIE_STT` picks: `whisper`/`local` and `gateway` are explicit and
    win over everything, `off` disables, and `auto` prefers the gateway *only when one is
    genuinely configured* (URL + key + SDK) and otherwise uses local whisper.

    Why `auto` also demands a KEY: `STT_BASE_URL` falls back to `LLM_BASE_URL`, so a URL
    alone says only "a brain is configured somewhere", not "send this child's voice
    there". With no key the gateway can only answer 401 — local whisper is the better
    ears, and an unset environment keeps behaving exactly as it did before this knob
    existed. (Since the brain endpoint stopped having a default, an unconfigured box
    resolves `STT_BASE_URL` to "" and never even considers the cloud ears.)

    The gateway is wrapped in a `FallbackTranscriber` whose standby is the rung it
    displaced: local whisper when installed, else a `NullTranscriber` that returns "".
    An outage then costs one reported downgrade instead of an exception on the turn's
    transcription path (mirrors `build_synthesizer`'s standby voice). The standby is
    built eagerly, so a box that must not load the whisper weights should simply not
    install faster-whisper — then a gateway outage means Moxie hears nothing until it
    returns, which the log says out loud.
    """
    from moxie_sdk import voice_settings
    from moxie_sdk.stt import (FallbackTranscriber, NullTranscriber, OpenAITranscriber,
                               WhisperTranscriber, make_openai_transcriber)
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


# --- 🎚️ the voice picker (docs/architecture/backlog/voice-picker.md) ----------------
# What this appliance can actually speak and hear with, and how to build any of it. The
# supervisor runtime holds one of these and never imports `config` itself, so a test can
# hand it a fake and the whole picker runs with no gateway, no key and no model wheels.

def gateway_model_ids():
    """Every model id the voice gateway lists — ONE `GET {MOXIE_VOICE_BASE_URL}/models`.

    Which of them is a voice and which is a pair of ears is decided by name, in
    `moxie_sdk/audio_models.py`: the listing itself says nothing (LiteLLM's
    `model_info.mode` is server-side config, not part of the public payload).
    """
    from openai import OpenAI                 # lazy — the SDK is an optional extra
    client = OpenAI(base_url=VOICE_BASE_URL, api_key=VOICE_API_KEY or "sk-local",
                    max_retries=0)
    return [getattr(m, "id", "") for m in (client.models.list().data or [])]


def local_piper_voices():
    """Local Piper voice names this box can really speak with — `[]` when the `piper`
    package is missing or no `.onnx` is installed. Both halves are required: a voice file
    with no runtime cannot speak, and a runtime with no voice file has nothing to say."""
    from moxie_sdk import voice_settings
    from moxie_sdk.tts import PiperSynthesizer
    if not PiperSynthesizer.available():
        return []
    return voice_settings.piper_voices(PIPER_MODEL, VOICES_DIR)


def local_whisper_models():
    """Local whisper sizes to offer — `[]` when faster-whisper is not installed.

    Only the default (`base.en`) and an explicitly configured `MOXIE_STT_MODEL`, never the
    whole catalogue: faster-whisper *accepts* any size but DOWNLOADS it on first use, and a
    dropdown that silently costs a 1.5 GB fetch mid-conversation is not an offer.
    """
    from moxie_sdk.stt import WhisperTranscriber
    if not WhisperTranscriber.available():
        return []
    names = [LOCAL_STT_MODEL]
    if STT_MODEL and STT_MODEL not in names:
        names.append(STT_MODEL)
    return names


def engine_pins() -> dict:
    """Which engine each side's env var pins right now — `""` where it pins nothing.

    One place reads `MOXIE_TTS`/`MOXIE_STT` for the picker, so the builders, the dropdown
    and the console's note can never disagree about what this deployment allows.
    """
    from moxie_sdk import voice_settings
    return {voice_settings.SPEECH:
            voice_settings.pin_for_env(voice_settings.SPEECH, TTS_ENGINE),
            voice_settings.LISTENING:
            voice_settings.pin_for_env(voice_settings.LISTENING, STT_ENABLED)}


class VoiceEngines:
    """The runtime's one seam onto this module for the 🎚️ picker.

    `available()` never blocks: the gateway listing is cached by `GatewayCatalog` and
    refreshed on a background thread, so the first call after boot answers with the local
    entries and `discovering: True` while the request is still in flight.
    """

    def __init__(self, catalog=None):
        from moxie_sdk import voice_settings
        self.catalog = catalog if catalog is not None else voice_settings.GatewayCatalog(
            gateway_model_ids if VOICE_BASE_URL else None,
            ttl_s=VOICE_DISCOVERY_TTL_S)

    def available(self, *, refresh: bool = False, settle_s: float = 0.0) -> dict:
        """`{available: {speech, listening}, pins, pin_notes, discovering, gateway_error}`.

        `settle_s` is the bounded wait a console WRITE may ask for so a cold supervisor
        validates a pick against the real list rather than against `tone` alone. A read
        passes 0 and never waits.

        The list is already **reduced to what an explicit `MOXIE_TTS`/`MOXIE_STT` would let
        this box install** (`voice_settings.filter_available`). Filtering here rather than
        in the browser is what makes the two halves agree by construction: the dropdown
        cannot show an entry the builders would then refuse, and a stale page that posts
        one is refused by the ordinary availability check with `pin_notes` saying why.
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
