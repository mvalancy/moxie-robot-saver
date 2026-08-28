# 🧩 Apps

Ready-made [`MoxieApp`](../app.py) implementations — the "brain" that decides what Moxie says and does.

- [`llm_app.py`](llm_app.py) — the default brain: drives Moxie from any OpenAI-compatible chat endpoint
  (local LiteLLM gateway, vLLM, Ollama, LM Studio…). Local-first.
- [`echo_app.py`](echo_app.py) — minimal app that echoes input; a template and a connectivity test.
- [`webhook_app.py`](webhook_app.py) — forwards turns to an external HTTP webhook.

---
📖 [SDK](../README.md) · [Back to top](../../../README.md)
