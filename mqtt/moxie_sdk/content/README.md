# 🧩 Content engine

Data-driven Moxie activities: the implementation of the
[content-module contract](../../../docs/architecture/content-module-contract.md). A module is JSON with
`conversations[]` (model-driven chats), `globals[]` (always-on regex commands) and `schedules[]`
(the day plan). Selected with `MOXIE_APP=content`.

- [`module.py`](module.py) — the data model and loader (`ContentModule`, `Conversation`, `Global`,
  `Schedule`). Pure.
- [`volley.py`](volley.py) — the per-turn `Volley` / `Session` API module code sees.
- [`render.py`](render.py) — prompt templating: a counting jinja2 `SandboxedEnvironment`, or a
  dependency-free fallback that never lets template syntax reach the brain.
- [`memory.py`](memory.py) — `session.summarize()`: the summary prompt, tolerant parse, and the
  filters on what may be remembered.
- [`content_app.py`](content_app.py) — `ContentApp`, the `MoxieApp` that runs a module through the AI
  seam.
- [`ext_host.py`](ext_host.py) — the sandboxed-extension *host*: builds the plain-JSON fact base,
  applies effects after the program ends, and bounds wire actions/events to closed tables.
- [`ext/`](ext/) — the sandboxed extension evaluator: a total JSON-AST language with a closed
  operator table, capability checks and English `explain()`
  ([brief](../../../docs/architecture/backlog/sandboxed-extensions.md)). A package whose import
  boundary (pure stdlib + its own siblings, nothing else) is a tested security property.
- [`packs/`](packs/) — 📦 content packs: export, review (the 2×2 that never clobbers a local
  edit), apply, the shipped ⊕ overlay merge, and authoring helpers
  ([packs](../../../docs/architecture/backlog/content-packs.md) ·
  [authoring](../../../docs/architecture/backlog/content-authoring.md)).

Security invariants are pinned by `sim/tests/test_ext_escapes.py`, `test_render_sandbox.py` and
`test_content_pack_sandbox.py`, and made load-bearing by the mutation tables in
[`sim/tools/`](../../../sim/tools/) — those anchor on exact code lines in these files, so keep
`python -m pytest sim/tests/test_mutation_tables.py` green when editing.

---
📖 [SDK](../README.md) · [Back to top](../../../README.md)
