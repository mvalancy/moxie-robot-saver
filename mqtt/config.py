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

# The host/IP the ROBOT uses to reach the broker (goes into the endpoint QR).
BROKER_PUBLIC_HOST = os.environ.get("MOXIE_BROKER_HOST", "192.168.1.9")
BROKER_PUBLIC_PORT = int(os.environ.get("MOXIE_BROKER_PORT", "8883"))

# --- which MoxieApp drives the robot ---
# "llm" (default), "echo", or "webhook"
MOXIE_APP = os.environ.get("MOXIE_APP", "llm")

# LLM app (OpenAI-compatible; local-first)
LLM_BASE_URL = os.environ.get("MOXIE_LLM_BASE_URL", "https://vps-gateway.chocolate-perch.ts.net/v1")
LLM_API_KEY  = os.environ.get("MOXIE_LLM_API_KEY", os.environ.get("LITELLM_MASTER_KEY", ""))
LLM_MODEL    = os.environ.get("MOXIE_LLM_MODEL", "qwen3.8-27b")

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
    return LLMApp(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, model=LLM_MODEL)
