"""
The per-turn `volley` / `session` API a content module's code sees
(docs/architecture/content-module-contract.md → "The volley / session API").

A `Volley` is one exchange (inbound context; `set_output`, `add_execution_action`,
subscriptions out); a `Session` is the whole conversation. Memory: `volley.persist_data`
is durable per-namespace storage (`store.MemoryStore`), `volley.local_data` is per-exchange
scratch, and `session.summarize(...)` is in `memory.py`.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from .memory import (FactList, summarize_history, wrap_facts,  # noqa: F401 (re-export)
                     DEFAULT_SUMMARY_PROMPT)


@dataclass
class Session:
    """The conversation across turns. `chat` (the injected brain) is optional; without it
    `summarize()` returns None."""
    history: list = field(default_factory=list)     # [{role, content}, ...]
    persist_data: dict = field(default_factory=dict)  # survives across sessions
    max_volleys: int = 40
    chat: Optional[object] = None                   # the brain, for summarize()
    module_id: str = ""
    content_id: str = ""
    conversation_id: str = ""

    @property
    def total_volleys(self) -> int:
        return sum(1 for m in self.history if m.get("role") == "user")

    def is_empty(self) -> bool:
        return not self.history

    @property
    def overflow(self) -> bool:
        """True once the turn budget is exceeded (content should wrap up)."""
        return self.total_volleys >= self.max_volleys

    # ---- memory ----
    def summarize(self, prompt_base: Optional[str] = None, *, chat=None,
                  append_transcript: bool = True, classifier=None, max_items: int = 5,
                  max_retries: int = 2, sleep=None) -> Optional[dict]:
        """LLM-summarize the transcript (the contract's `session.summarize(...)`) into a
        filtered `{"facts", "preferences", "open_threads", "summary"}` dict, or None.
        Structured (unlike OpenMoxie's string) so a parent can delete one item."""
        return summarize_history(self.history, chat if chat is not None else self.chat,
                                 prompt_base=prompt_base,
                                 append_transcript=append_transcript,
                                 classifier=classifier, max_items=max_items,
                                 max_retries=max_retries, sleep=sleep)


class Volley:
    """One exchange. Inbound: speech, request/input_vars, config, entities. Outbound:
    set_output(text, markup) + add_execution_action(name, args) + subscriptions."""

    def __init__(self, speech: str = "", *, config: Optional[dict] = None,
                 request: Optional[dict] = None, entities: Optional[list] = None,
                 persist_data: Optional[dict] = None, local_data: Optional[dict] = None):
        self.speech = speech
        self.config = config or {}              # {child_pii: {...}, ...}
        self.request = request or {}            # {input_vars: {...}, ...}
        self.entities = entities or []          # regex capture groups (globals)
        self.persist_data = persist_data if persist_data is not None else {}
        self.local_data = local_data if local_data is not None else {}
        # outbound
        self.output_text: Optional[str] = None
        self.output_markup: Optional[str] = None
        self.execution_actions: list = []       # [{name, args}]
        self.subscriptions: list = []           # robot events to subscribe to

    # ---- outbound API ----
    def set_output(self, text: str, markup: Optional[str] = None) -> None:
        self.output_text = text
        self.output_markup = markup

    def add_execution_action(self, name: str, args=None) -> None:
        """Ask the robot to *do* something (eb_timer_request, eb_enable_qr, …)."""
        self.execution_actions.append({"name": name, "args": args or []})

    def update_subscriptions(self, events) -> None:
        """Subscribe to robot input events — **replacing** the list (the contract's verb,
        for our own trusted handlers)."""
        self.subscriptions = list(events or [])

    def add_subscriptions(self, events) -> None:
        """Add events to this volley's list (order kept, duplicates dropped). The only form
        a sandboxed extension gets: it may add a perception, never remove one."""
        for e in events or []:
            if e not in self.subscriptions:
                self.subscriptions.append(e)

    # ---- inbound convenience ----
    def input_var(self, key: str, default=None):
        """A value from RemoteChatRequest.input_vars (e.g. '$eb_qr_value')."""
        return self.request.get("input_vars", {}).get(key, default)
