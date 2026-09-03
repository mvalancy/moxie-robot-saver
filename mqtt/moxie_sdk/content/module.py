"""
Content-module data model + loader.

A module is JSON with three optional sections (docs/architecture/content-module-contract.md):
  conversations[] — LLM-driven chats (Jinja prompt + persona + optional code hooks)
  globals[]       — regex-triggered commands, always on (timers, "stop", …)
  schedules[]     — the day's plan of activities

Every record also carries `source_version` — the **pack author's** own counter for that
one item (default 1). It is what makes an upgrade distinguishable from a re-import
(`packs.py`, docs/architecture/backlog/content-packs.md §2.3); the engine itself never
reads it.

This module is pure (no MQTT/LLM) so it is fully unit-testable.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional


def _source_version(d: dict) -> int:
    """A record's author-owned version counter — 1 when it says nothing, which is what
    every module file written before content packs existed says."""
    v = (d or {}).get("source_version", 1)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 1
        if v < 0:
            return 1
    return v


@dataclass
class Conversation:
    """An LLM-driven chat activity."""
    name: str = ""
    module_id: str = ""
    content_id: str = ""
    prompt: str = ""                     # Jinja2-templated over volley/session
    opener: str = ""                     # optional first line ('|'-alternatives + tags)
    model: Optional[str] = None
    max_tokens: int = 200
    temperature: float = 0.8
    max_history: int = 40
    max_volleys: int = 40
    code: str = ""                       # optional Python hooks (pre/post_process, …)
    memory: dict = field(default_factory=dict)   # see `memory_namespace` below
    source_version: int = 1              # the author's counter for this item (packs.py)

    # ---- long-term memory (the contract's persist_data / session.summarize) ----
    # OpenMoxie's MemoryChat drives this from a `code` string
    # (`complete_handler` → `session.summarize()` → `volley.persist_data`). We do not
    # execute module `code` (sandboxing, see content_app.py), so the same behaviour is
    # declared instead:
    #
    #     "memory": {"namespace": "memory_chat", "summarize": true, "min_volleys": 2,
    #                "max_items": 5, "prompt": "<optional override instruction>"}
    #
    # `namespace` alone is enough to make `{{ volley.persist_data.<ns>.* }}` resolve.

    @property
    def memory_namespace(self) -> str:
        """The `persist_data` namespace this conversation reads/writes ("" = none)."""
        return str((self.memory or {}).get("namespace") or "")

    @property
    def summarizes(self) -> bool:
        """True when this conversation writes a summary when it ends."""
        return bool(self.memory_namespace) and bool((self.memory or {}).get("summarize", True))

    @classmethod
    def from_dict(cls, d: dict) -> "Conversation":
        return cls(
            name=str(d.get("name", "")),
            module_id=str(d.get("module_id", "")),
            content_id=str(d.get("content_id", "")),
            prompt=str(d.get("prompt", "")),
            opener=str(d.get("opener", "")),
            model=d.get("model"),
            max_tokens=int(d.get("max_tokens", 200)),
            temperature=float(d.get("temperature", 0.8)),
            max_history=int(d.get("max_history", 40)),
            max_volleys=int(d.get("max_volleys", 40)),
            code=str(d.get("code", "")),
            memory=dict(d.get("memory") or {}),
            source_version=_source_version(d),
        )


@dataclass
class Global:
    """A regex-triggered command, active regardless of the running activity."""
    name: str = ""
    pattern: str = ""
    entity_groups: str = ""              # e.g. "3,4" — which capture groups are entities
    action: int = 0
    code: str = ""
    source_version: int = 1              # the author's counter for this item (packs.py)
    _rx: Optional[re.Pattern] = field(default=None, repr=False)

    @classmethod
    def from_dict(cls, d: dict) -> "Global":
        g = cls(
            name=str(d.get("name", "")),
            pattern=str(d.get("pattern", "")),
            entity_groups=str(d.get("entity_groups", "")),
            action=int(d.get("action", 0)),
            code=str(d.get("code", "")),
            source_version=_source_version(d),
        )
        if g.pattern:
            g._rx = re.compile(g.pattern, re.I)
        return g

    def match(self, utterance: str) -> Optional[list]:
        """Return the entity capture-group values if this global fires, else None.

        `entity_groups` names which groups are entities (e.g. "3,4"); if unset, all
        captured groups are returned. A non-None result (even []) means it matched."""
        if not self._rx:
            return None
        m = self._rx.search(utterance or "")
        if not m:
            return None
        groups = m.groups()
        if self.entity_groups.strip():
            idx = [int(x) for x in self.entity_groups.split(",") if x.strip()]
            return [m.group(i) for i in idx]
        return list(groups)


@dataclass
class Schedule:
    """The day's plan — mirrors embodied.robotbrain.ContentSchedule."""
    name: str = ""
    schedule: dict = field(default_factory=dict)
    source_version: int = 1              # the author's counter for this item (packs.py)

    @classmethod
    def from_dict(cls, d: dict) -> "Schedule":
        return cls(name=str(d.get("name", "")), schedule=dict(d.get("schedule", {})),
                   source_version=_source_version(d))


@dataclass
class ContentModule:
    """A loaded content module: its conversations, globals, and schedules."""
    conversations: list = field(default_factory=list)   # list[Conversation]
    globals: list = field(default_factory=list)         # list[Global]
    schedules: list = field(default_factory=list)       # list[Schedule]

    def conversation(self, module_id: str, content_id: str = "") -> Optional[Conversation]:
        """Find a conversation by module_id (+ optional content_id)."""
        for c in self.conversations:
            if c.module_id == module_id and (not content_id or c.content_id == content_id):
                return c
        return None

    def match_global(self, utterance: str):
        """First global whose regex fires → (Global, entities); else None."""
        for g in self.globals:
            ents = g.match(utterance)
            if ents is not None:
                return g, ents
        return None


def load_module(d: dict) -> ContentModule:
    """Parse one module JSON object into a ContentModule."""
    return ContentModule(
        conversations=[Conversation.from_dict(c) for c in d.get("conversations", []) or []],
        globals=[Global.from_dict(g) for g in d.get("globals", []) or []],
        schedules=[Schedule.from_dict(s) for s in d.get("schedules", []) or []],
    )


def load_modules(data) -> ContentModule:
    """Load one module dict, or a list of module dicts merged into one ContentModule."""
    if isinstance(data, dict):
        return load_module(data)
    merged = ContentModule()
    for d in data or []:
        m = load_module(d)
        merged.conversations += m.conversations
        merged.globals += m.globals
        merged.schedules += m.schedules
    return merged
