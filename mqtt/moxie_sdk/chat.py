"""
The LLM chat boundary — a `chat(messages) -> str` callable over any OpenAI-compatible
endpoint (docs/architecture/ai-seam.md §2). ContentApp and LLMApp both drive the brain
through this seam; keeping it here means one place understands the endpoint + what
counts as "offline" (endpoint unreachable → the caller signals ERROR_OFFLINE).
"""
from __future__ import annotations
from typing import Callable

ChatFn = Callable[[list], str]      # messages [{role,content}] -> assistant text


def is_offline_error(e: Exception) -> bool:
    """True when the endpoint is unreachable (connection/timeout) vs a soft error.
    Matched by type-name so the SDK keeps no hard dependency on openai's classes."""
    names = {type(e).__name__} | {b.__name__ for b in type(e).__mro__}
    return bool(names & {"APIConnectionError", "APITimeoutError", "ConnectionError",
                         "ConnectError", "Timeout", "TimeoutError"})


def make_openai_chat(base_url: str, api_key: str, model: str = "gpt-4o-mini",
                     max_tokens: int = 200, temperature: float = 0.8) -> ChatFn:
    """Build a chat(messages)->str over an OpenAI-compatible endpoint. Raises on
    failure (the caller decides offline vs soft — see is_offline_error)."""
    from openai import OpenAI          # lazy import so the SDK has no hard dep
    client = OpenAI(base_url=base_url, api_key=api_key or "sk-local")

    def chat(messages: list) -> str:
        resp = client.chat.completions.create(
            model=model, messages=messages,
            max_tokens=max_tokens, temperature=temperature)
        return (resp.choices[0].message.content or "").strip()

    return chat
