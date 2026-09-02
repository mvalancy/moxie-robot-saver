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
TTS_VOICE      = os.environ.get("MOXIE_TTS_VOICE", "alloy")

# STT: local faster-whisper. "auto" = enable when installed; "off" to disable.
STT_ENABLED = os.environ.get("MOXIE_STT", "auto").lower()
STT_MODEL   = os.environ.get("MOXIE_STT_MODEL", "base.en")

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
    """A server voice (moxie_sdk.tts.Synthesizer) if MOXIE_VOICE_BASE_URL is set, else
    None (a real robot self-synthesizes; the SIM needs this for audio)."""
    from moxie_sdk.tts import make_voice_synthesizer
    return make_voice_synthesizer(VOICE_BASE_URL, VOICE_API_KEY, TTS_VOICE)


def build_transcriber():
    """A local STT engine (moxie_sdk.stt.WhisperTranscriber) when enabled + installed,
    else None (text turns still work; audio frames are ignored)."""
    if STT_ENABLED == "off":
        return None
    from moxie_sdk.stt import WhisperTranscriber
    if not WhisperTranscriber.available():
        return None
    return WhisperTranscriber(model=STT_MODEL)
