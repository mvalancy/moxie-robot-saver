# `content_modules/` — authored Moxie activities

Data-driven content modules loaded by the [content engine](../moxie_sdk/content/)
(`ContentApp`). A module is JSON with `conversations[]` / `globals[]` / `schedules[]`
— see [the content-module contract](../../docs/architecture/content-module-contract.md).

- [`starter.json`](starter.json) — a friendly free-chat conversation (`FREE_CHAT`) +
  a timer global. Run it: `MOXIE_APP=content MOXIE_CONTENT_MODULE=content_modules/starter.json python run.py`.
- [`memory_chat.json`](memory_chat.json) — a chat that **remembers** (`MEMORY_CHAT`), plus an
  "About Me" content id (`MEMORY_CHAT/aboutme`) where the child can ask what Moxie knows.
  Each conversation summarizes itself when it ends and merges a few durable facts into
  `volley.persist_data.memory_chat`; the next conversation reads them back.
  Run it: `MOXIE_APP=content MOXIE_CONTENT_MODULE=content_modules/memory_chat.json python run.py`.
  *Pattern from OpenMoxie's `MemoryChat.json` (MIT) — the idea of module-namespaced facts
  written by a `complete_handler` is theirs; these prompts, the declarative `memory` block
  and the structured summary are ours.*

## The `memory` block

Because module `code` strings are deliberately never executed (sandboxing —
[`content_app.py`](../moxie_sdk/content/content_app.py)), a conversation **declares** its
memory instead of scripting it:

```json
"memory": {"namespace": "memory_chat", "summarize": true, "min_volleys": 2,
           "max_items": 5, "prompt": "<optional instruction override>"}
```

`namespace` alone makes `{{ volley.persist_data.<namespace>.* }}` resolve in the prompt.
With `summarize` (the default when a namespace is set), the end of the conversation —
an `<exit>`, a module switch, or the robot going offline — asks the brain for a short
structured summary and merges it in with provenance. What is remembered is bounded,
policy-gated (`LoggingPolicy.NO_DATA` → nothing is written) and erasable by a parent
(`GET`/`DELETE /memory` on the supervisor's status port).

Add an activity by dropping another `.json` here and pointing `MOXIE_CONTENT_MODULE` at it
(or a directory to merge — a future loader slice).
