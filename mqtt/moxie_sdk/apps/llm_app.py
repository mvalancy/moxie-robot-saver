"""
LLMApp — the default Moxie brain. Drives Moxie from any OpenAI-compatible chat
endpoint (a local LiteLLM gateway, vLLM, Ollama, LM Studio, …). Local-first; never
hard-wired to a vendor. This is the reference implementation of a MoxieApp.
"""
from __future__ import annotations
from ..app import MoxieApp
from ..types import Turn, Reply, RobotContext

DEFAULT_PERSONA = (
    "You are Moxie, a warm, playful, encouraging robot companion for a child. "
    "Speak in one to three short, natural sentences. Be curious and kind, never "
    "preachy. Use simple language a young child understands. You are physically "
    "present in the room with them."
)


class LLMApp(MoxieApp):
    name = "llm"

    def __init__(self, base_url: str, api_key: str, model: str = "qwen3.8-27b",
                 persona: str = DEFAULT_PERSONA, max_tokens: int = 120,
                 temperature: float = 0.7, max_history: int = 12):
        from openai import OpenAI          # imported lazily so the SDK has no hard dep
        self._client = OpenAI(base_url=base_url, api_key=api_key or "sk-local")
        self._model = model
        self._persona = persona
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._max_history = max_history

    def _system(self, robot: RobotContext) -> str:
        c = robot.child
        who = f"\nYou are talking to {c.nickname}."
        if c.pronouns:
            who += f" Their pronouns are {c.pronouns}."
        if c.notes:
            who += f" Context: {c.notes}"
        return self._persona + who

    def greeting(self, robot: RobotContext) -> Reply:
        return Reply(text=f"Hi {robot.child.nickname}! It's so good to see you. "
                          f"What's on your mind today?")

    def respond(self, turn: Turn) -> Reply:
        messages = [{"role": "system", "content": self._system(turn.robot)}]
        messages += turn.history[-self._max_history:]
        messages.append({"role": "user", "content": turn.speech})
        try:
            resp = self._client.chat.completions.create(
                model=self._model, messages=messages,
                max_tokens=self._max_tokens, temperature=self._temperature)
            text = (resp.choices[0].message.content or "").strip()
        except Exception as e:
            return Reply(text="Hmm, my brain got a little fuzzy. Can you say that again?",
                         end_turn=False)
        return Reply(text=text or "Tell me more!")
