"""
ContentApp — runs a content module through the AI seam (docs/architecture/
content-module-contract.md). This is where the pure engine (module/volley/render)
becomes a live MoxieApp: each turn it checks `globals[]` first (always-on commands),
otherwise runs the active `conversations[]` module — render its Jinja prompt over the
volley, hand it to the brain, return a Reply.

The brain is injected as a `chat(messages) -> str` callable (the AI-seam boundary),
so ContentApp is testable without a real LLM and works with any OpenAI-compatible
endpoint. Global handlers are registered Python callables keyed by the global's name
(arbitrary `code`-string execution from module JSON is deliberately NOT done here —
a sandboxing concern deferred; built-in/registered handlers cover the safe cases).
"""
from __future__ import annotations
from typing import Callable, Optional

from ..app import MoxieApp
from ..types import Turn, Reply, RobotContext
from .module import ContentModule
from .volley import Volley, Session
from .render import render_prompt

ChatFn = Callable[[list], str]          # messages [{role,content}] -> assistant text
GlobalHandler = Callable[[Volley, Session], None]   # sets volley.output / actions


def _child_pii(robot: RobotContext) -> dict:
    """The child profile as the volley/prompt sees it (`volley.config.child_pii`)."""
    c = robot.child
    return {"nickname": c.nickname, "pronouns": c.pronouns,
            "birthday": c.birthday_iso, "notes": c.notes}


class ContentApp(MoxieApp):
    name = "content"

    def __init__(self, module: ContentModule, chat: ChatFn, *, persona: str = "",
                 default_module_id: Optional[str] = None,
                 global_handlers: Optional[dict] = None):
        self.module = module
        self._chat = chat
        self._persona = persona
        self._default_module_id = default_module_id
        self._handlers: dict = dict(global_handlers or {})

    def register_global(self, name: str, handler: GlobalHandler) -> None:
        self._handlers[name] = handler

    # ---- helpers ----
    def _volley(self, turn: Turn, entities=None) -> Volley:
        return Volley(speech=turn.speech, config={"child_pii": _child_pii(turn.robot)},
                      request={"input_vars": turn.input_vars}, entities=entities or [])

    def _active_conversation(self, turn: Turn):
        mid = turn.robot.module_id or self._default_module_id
        conv = self.module.conversation(mid, turn.robot.content_id or "") if mid else None
        if conv is None and self.module.conversations:
            conv = self.module.conversations[0]      # fall back to the first
        return conv

    @staticmethod
    def _reply_from_volley(v: Volley) -> Reply:
        # M2: a global handler drives text/markup. Plumbing volley.execution_actions
        # (eb_timer_request etc.) into RemoteChatAction is a later slice.
        return Reply(text=v.output_text or "", markup=v.output_markup)

    # ---- MoxieApp ----
    def greeting(self, robot: RobotContext) -> Optional[Reply]:
        conv = self._active_conversation(Turn(robot=robot, speech=""))
        if conv and conv.opener:
            line = conv.opener.split("|")[0].strip()
            # strip inline tags like <opener>
            line = line.replace("<opener>", "").strip()
            if line:
                return Reply(text=line)
        return None

    def respond(self, turn: Turn) -> Reply:
        # 1) globals first — always-on commands (timers, "stop", …)
        hit = self.module.match_global(turn.speech)
        if hit is not None:
            g, entities = hit
            handler = self._handlers.get(g.name)
            if handler:
                v = self._volley(turn, entities=entities)
                session = Session(history=list(turn.history))
                handler(v, session)
                if v.output_text is not None or v.execution_actions:
                    return self._reply_from_volley(v)
            # matched but no handler produced output → fall through to conversation

        # 2) the active conversation module
        conv = self._active_conversation(turn)
        if conv is None:
            return Reply(text="Let's chat! What's on your mind?")
        v = self._volley(turn)
        session = Session(history=list(turn.history), max_volleys=conv.max_volleys)
        system = render_prompt(conv.prompt, {"volley": v, "session": session})
        if self._persona:
            system = f"{self._persona}\n\n{system}" if system else self._persona
        messages = [{"role": "system", "content": system}]
        messages += turn.history[-conv.max_history:]
        messages.append({"role": "user", "content": turn.speech})
        text = (self._chat(messages) or "").strip()
        if not text:
            return Reply(text="Tell me more!")
        return Reply(text=text)
