"""
Content-module engine — data-driven Moxie activities.

Implements docs/architecture/content-module-contract.md: a module is JSON with
optional `conversations[]` (LLM chats), `globals[]` (regex commands), and
`schedules[]` (the day's plan). This package is the pure model + loader + the
per-turn volley/session context + prompt rendering; ContentApp (content_app.py)
runs a module through the AI seam.
"""
from .module import ContentModule, Conversation, Global, Schedule, load_module, load_modules
from .volley import Volley, Session
from .render import render_prompt
from .content_app import ContentApp

__all__ = ["ContentModule", "Conversation", "Global", "Schedule",
           "load_module", "load_modules", "Volley", "Session", "render_prompt",
           "ContentApp"]
