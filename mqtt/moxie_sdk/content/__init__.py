"""
Content-module engine — data-driven Moxie activities.

Implements docs/architecture/content-module-contract.md: a module is JSON with
optional `conversations[]` (LLM chats), `globals[]` (regex commands), and
`schedules[]` (the day's plan). This package is the pure model + loader + the
per-turn volley/session context + prompt rendering + the long-term memory behind
`volley.persist_data` / `session.summarize()` (memory.py); ContentApp
(content_app.py) runs a module through the AI seam; packs.py is 📦 the distribution
unit — export, import-with-review and the shipped ⊕ overlay merge.
"""
from .module import ContentModule, Conversation, Global, Schedule, load_module, load_modules
from .volley import Volley, Session
from .render import render_prompt
from .memory import FactList, wrap_facts, summarize_history, provenance
from .content_app import ContentApp
from . import packs
from .packs import (PackError, apply_pack, build_module, export_pack, inventory,
                    merge_items, module_data, parse_pack, review_pack, scan_outgoing,
                    shipped_items)

__all__ = ["ContentModule", "Conversation", "Global", "Schedule",
           "load_module", "load_modules", "Volley", "Session", "render_prompt",
           "FactList", "wrap_facts", "summarize_history", "provenance",
           "ContentApp", "packs", "PackError", "apply_pack", "build_module",
           "export_pack", "inventory", "merge_items", "module_data", "parse_pack",
           "review_pack", "scan_outgoing", "shipped_items"]
