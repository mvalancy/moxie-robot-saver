"""
The LLM chat boundary — a `chat(messages) -> str` callable over any OpenAI-compatible
endpoint (docs/architecture/ai-seam.md §2), with graceful rate-limit/backoff + pacing
so a busy gateway slows us down instead of failing the child.

ContentApp and LLMApp drive the brain through this seam; keeping it here means one
place understands the endpoint, what "offline" means (endpoint unreachable → the
caller signals ERROR_OFFLINE), and how to back off when the gateway rate-limits.
"""
from __future__ import annotations
import os
import random
import threading
import time
from typing import Callable, Iterator, Optional

ChatFn = Callable[[list], str]      # messages [{role,content}] -> assistant text
StreamFn = Callable[[list], Iterator[str]]   # messages -> a trickle of text deltas


# ---- the recorded upstream-call counter ----------------------------------- #
#
# 💰 Several paths must never cost a model call (loudest: presence — a vision event is
# never routed to a brain, vision.md §7.1). A stub brain that fails when called proves
# only that *that* double was quiet, not that no retry or second path spent a request. So,
# like the edge's `functions/api/_lib/limits.js::noteUpstreamCall()`, this counter sits
# immediately before the call that becomes an HTTP request, and tests assert on the RECORD.
#
# One increment per *request attempt* on chat/completions (a backoff retry counts again;
# a stream counts once on open). TTS/STT gateway calls are a different budget and are not
# counted — hence `model`, not `gateway`. Process-global (turns run on a worker pool); a
# diagnostic, never a billing ledger.

_MODEL_CALLS = {"chat": 0, "stream": 0}

#: The same record seen from ONE thread. The console's *Try it* runs its turn on a worker
#: of its own, so it can read exactly what that turn spent and how its last request ended
#: while robots' turns move the process-wide counter beside it.
_THREAD = threading.local()


class ModelCallBudgetExceeded(RuntimeError):
    """Raised before an outbound model request would exceed a process campaign cap."""


def _model_call_limit() -> Optional[int]:
    """Return the opt-in process campaign cap, failing closed on invalid values."""
    raw = os.environ.get("MOXIE_MODEL_CALL_LIMIT", "").strip()
    if not raw:
        return None
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ModelCallBudgetExceeded("MOXIE_MODEL_CALL_LIMIT must be a positive integer") from exc
    if limit < 1:
        raise ModelCallBudgetExceeded("MOXIE_MODEL_CALL_LIMIT must be a positive integer")
    return limit


def note_model_call(kind: str = "chat") -> None:
    """Record one request attempt against the model endpoint. Called immediately before
    the call that performs it — never after, so a call that raises is still counted."""
    limit = _model_call_limit()
    attempted = model_calls()
    if limit is not None and attempted >= limit:
        raise ModelCallBudgetExceeded(
            f"model-call campaign limit exhausted ({attempted}/{limit})")
    _MODEL_CALLS[kind] = _MODEL_CALLS.get(kind, 0) + 1
    _THREAD.calls = thread_model_calls() + 1


def model_calls(kind: str = "") -> int:
    """How many model requests this process has attempted (`kind=""` → all of them)."""
    if kind:
        return int(_MODEL_CALLS.get(kind, 0))
    return sum(_MODEL_CALLS.values())


def thread_model_calls() -> int:
    """How many model requests THIS thread has attempted (`note_model_call`)."""
    return int(getattr(_THREAD, "calls", 0))


def last_call_error() -> Optional[Exception]:
    """How this thread's most recent `call_with_backoff` ended: the exception it gave up
    on, or None when it succeeded. An app turns that failure into a friendly line for the
    child (`LLMApp.respond`); this keeps the reason for whoever is asking why."""
    return getattr(_THREAD, "error", None)


def reset_model_calls() -> None:
    """Zero the counter. For tests, which take a reading before and after."""
    for key in list(_MODEL_CALLS):
        _MODEL_CALLS[key] = 0


# ---- error classification ------------------------------------------------- #

def _mro_names(e: Exception) -> set:
    return {type(e).__name__} | {b.__name__ for b in type(e).__mro__}


def is_offline_error(e: Exception) -> bool:
    """Endpoint unreachable (connection/timeout) vs a soft error. Matched by
    type-name so the SDK keeps no hard dependency on openai's exception classes."""
    return bool(_mro_names(e) & {"APIConnectionError", "APITimeoutError",
                                 "ConnectionError", "ConnectError", "Timeout",
                                 "TimeoutError"})


def _status_code(e: Exception):
    return (getattr(e, "status_code", None)
            or getattr(getattr(e, "response", None), "status_code", None))


def is_rate_limit_error(e: Exception) -> bool:
    """The gateway is throttling us (HTTP 429 / RateLimitError)."""
    return "RateLimitError" in _mro_names(e) or _status_code(e) == 429


def is_server_error(e: Exception) -> bool:
    """A transient 5xx from the gateway (worth a retry)."""
    sc = _status_code(e)
    return bool(sc) and 500 <= sc < 600


def retry_after_seconds(e: Exception) -> Optional[float]:
    """Honor a Retry-After header if the gateway sent one."""
    resp = getattr(e, "response", None)
    try:
        ra = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
        return float(ra) if ra else None
    except Exception:
        return None


# ---- adaptive pacing ------------------------------------------------------ #

class Pacer:
    """A gentle self-throttle: after the gateway rate-limits, enforce a minimum gap
    before the next request that GROWS on each limit and DECAYS on success — so we
    naturally slow down when the server is busy and speed back up when it recovers."""

    def __init__(self, *, grow=2.0, decay=0.5, max_gap=8.0, sleep=time.sleep,
                 clock=time.monotonic):
        self.min_gap = 0.0
        self._grow, self._decay, self._max = grow, decay, max_gap
        self._sleep, self._clock = sleep, clock
        self._last = 0.0

    def before_request(self):
        if self.min_gap <= 0:
            return
        wait = self.min_gap - (self._clock() - self._last)
        if wait > 0:
            self._sleep(wait)

    def on_success(self):
        self._last = self._clock()
        self.min_gap = max(0.0, self.min_gap * self._decay)
        if self.min_gap < 0.05:
            self.min_gap = 0.0

    def on_rate_limit(self):
        self._last = self._clock()
        self.min_gap = min(self._max, (self.min_gap or 0.5) * self._grow)


# ---- retry with backoff --------------------------------------------------- #

def call_with_backoff(fn, *, max_retries=4, base=0.6, cap=20.0, on_backoff=None,
                      pacer: Optional[Pacer] = None, sleep=time.sleep):
    """Call `fn()`, retrying transient failures (rate-limit / 5xx / connection) with
    exponential backoff + jitter, honoring Retry-After. `on_backoff(attempt, delay,
    err)` is invoked before each wait (for clean logging/status). A non-transient
    error, or exhausting `max_retries`, re-raises the last error."""
    attempt = 0
    while True:
        if pacer:
            pacer.before_request()
        try:
            out = fn()
            if pacer:
                pacer.on_success()
            _THREAD.error = None
            return out
        except Exception as e:
            rate_limited = is_rate_limit_error(e)
            if pacer and rate_limited:
                pacer.on_rate_limit()
            transient = rate_limited or is_server_error(e) or is_offline_error(e)
            if not transient or attempt >= max_retries:
                _THREAD.error = e
                raise
            ra = retry_after_seconds(e)
            delay = ra if ra is not None else min(cap, base * (2 ** attempt)) + random.uniform(0, base)
            if on_backoff:
                on_backoff(attempt + 1, delay, e)
            sleep(delay)
            attempt += 1


def _default_on_backoff(attempt, delay, err):
    why = "rate-limited (429)" if is_rate_limit_error(err) else type(err).__name__
    print(f"[gateway] busy — {why}; slowing down {delay:.1f}s (retry {attempt})",
          flush=True)


def make_openai_chat(base_url: str, api_key: str, model: str = "graphling-medium",
                     max_tokens: int = 200, temperature: float = 0.8, *,
                     max_retries: int = 4, on_backoff=_default_on_backoff,
                     pacer: Optional[Pacer] = None, client=None) -> ChatFn:
    """Build a chat(messages)->str over an OpenAI-compatible endpoint, with graceful
    rate-limit backoff + adaptive pacing. Raises on failure after retries (the caller
    decides offline vs rate-limited vs soft — see the is_* helpers).

    `client` is the injection seam: anything exposing `.chat.completions.create(...)`, so
    a test drives this real function (counter and backoff included) with no socket."""
    if client is None:
        from openai import OpenAI      # lazy import so the SDK has no hard dep
        client = OpenAI(base_url=base_url, api_key=api_key or "sk-local", max_retries=0)
    _pacer = pacer if pacer is not None else Pacer()

    def chat(messages: list) -> str:
        def _once():
            # Before the request: a call that raises still cost an attempt.
            note_model_call("chat")
            resp = client.chat.completions.create(
                model=model, messages=messages,
                max_tokens=max_tokens, temperature=temperature)
            return (resp.choices[0].message.content or "").strip()
        return call_with_backoff(_once, max_retries=max_retries,
                                 on_backoff=on_backoff, pacer=_pacer)

    return chat


# ---- streaming ------------------------------------------------------------ #
# The same seam, one token at a time: a whole completion can take 18-45 s, but its first
# sentence is ready after a few tokens, so the runtime speaks at first-token latency
# (`segment.py` cuts sentences; each goes out as its own RemoteChatResponse chunk).

def delta_text(event) -> str:
    """The text carried by one streamed chunk, or "" (SDK objects or plain dicts)."""
    if event is None:
        return ""
    if isinstance(event, str):
        return event
    if isinstance(event, dict):
        choices = event.get("choices") or []
        if not choices:
            return ""
        first = choices[0] or {}
        delta = first.get("delta") or {}
        if isinstance(delta, dict):
            return delta.get("content") or ""
        return getattr(delta, "content", "") or ""
    choices = getattr(event, "choices", None) or []
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    return (getattr(delta, "content", None) or "") if delta is not None else ""


def stream_completion(client, model: str, messages: list, *, max_tokens: int = 200,
                      temperature: float = 0.8, max_retries: int = 4,
                      on_backoff=_default_on_backoff,
                      pacer: Optional[Pacer] = None) -> Iterator[str]:
    """Yield the text deltas of one streaming chat completion.

    Backoff wraps **opening** the stream (where 429/5xx surface and a retry is free).
    Once open, a mid-stream error propagates; the caller falls back
    (`LLMApp.respond_stream`)."""
    def _open():
        # One increment per opening attempt (deltas are the same request).
        note_model_call("stream")
        return client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens,
            temperature=temperature, stream=True)

    stream = call_with_backoff(_open, max_retries=max_retries,
                               on_backoff=on_backoff, pacer=pacer)
    try:
        for event in stream:
            text = delta_text(event)
            if text:
                yield text
    finally:
        # A cancelled turn closes the generator; let go of the HTTP response too.
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def make_openai_stream(base_url: str, api_key: str, model: str = "graphling-medium",
                       max_tokens: int = 200, temperature: float = 0.8, *,
                       max_retries: int = 4, on_backoff=_default_on_backoff,
                       pacer: Optional[Pacer] = None, client=None) -> StreamFn:
    """`make_openai_chat`'s streaming twin: `stream(messages) -> Iterator[str]`.

    `client` is the same rule-9 seam, for the same reason."""
    if client is None:
        from openai import OpenAI      # lazy import so the SDK has no hard dep
        client = OpenAI(base_url=base_url, api_key=api_key or "sk-local", max_retries=0)
    _pacer = pacer if pacer is not None else Pacer()

    def stream(messages: list) -> Iterator[str]:
        return stream_completion(client, model, messages, max_tokens=max_tokens,
                                 temperature=temperature, max_retries=max_retries,
                                 on_backoff=on_backoff, pacer=_pacer)

    return stream
