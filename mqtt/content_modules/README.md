# `content_modules/` — authored Moxie activities

Data-driven content modules loaded by the [content engine](../moxie_sdk/content/)
(`ContentApp`). A module is JSON with `conversations[]` / `globals[]` / `schedules[]`
— see [the content-module contract](../../docs/architecture/content-module-contract.md).

- [`starter.json`](starter.json) — a friendly free-chat conversation (`FREE_CHAT`), a timer
  global, and **`What Time Is It`** — the first shipped activity whose behaviour is a
  *program* rather than a prompt (see "The `extension` block" below). Run it: `MOXIE_APP=content MOXIE_CONTENT_MODULE=content_modules/starter.json python run.py`.
- [`memory_chat.json`](memory_chat.json) — a chat that **remembers** (`MEMORY_CHAT`), plus an
  "About Me" content id (`MEMORY_CHAT/aboutme`) where the child can ask what Moxie knows.
  Each conversation summarizes itself when it ends and merges a few durable facts into
  `volley.persist_data.memory_chat`; the next conversation reads them back.
  Run it: `MOXIE_APP=content MOXIE_CONTENT_MODULE=content_modules/memory_chat.json python run.py`.
  *Pattern from OpenMoxie's `MemoryChat.json` (MIT) — the idea of module-namespaced facts
  written by a `complete_handler` is theirs; these prompts, the declarative `memory` block
  and the structured summary are ours.*

## The `extension` block

*(BEYOND #6 P0, 2026-09-03. Design:
[`backlog/sandboxed-extensions.md`](../../docs/architecture/backlog/sandboxed-extensions.md);
evaluator: [`ext.py`](../moxie_sdk/content/ext.py).)*

A `global` or a `conversation` may carry a small **program** — a rule list over a total
JSON-AST expression language with no `exec`, no parser, no loops and no reachable host
object, behind a capability set that must **equal** what the program uses:

```json
"extension": {"ext_format": 1, "capabilities": ["clock", "handled", "say"], "on": "global",
              "rules": [{"let": {"h24": {"get": [{"clock.local": []}, "hour", 0]}},
                         "do": [{"say": {"concat": ["It is hour ",
                                                    {"str": [{"var": "h24"}]}]}},
                                {"handled": true}]}]}
```

`What Time Is It` in `starter.json` is the real one: a hand-port of OpenMoxie's `MoxieTime`
(MIT, © Justin Beghtol — re-authored, never copied), byte-identical to row **G1** of
[`sim/tests/data/ext_conformance.json`](../../sim/tests/data/README.md), answering *"what
time is it"* with **no model call**.

Two things worth knowing before you author one:

- **It reads back as English.** `ext.explain()` renders each rule as a sentence and
  `ext.grant_list()` renders each capability from a fixed table, and both appear in the
  pack review. If your rule does not read well as a sentence, a parent cannot review it.
- **`clock` is not granted by default.** Only `{say, handled, session, child.nickname}`
  are. A *shipped* activity gets more because the wider set is anchored to the **digest of
  the program** (`content_app.SHIPPED_EXTRA_GRANTS`), so an imported pack that overrides a
  shipped item's key does not inherit its grants. An imported activity needing `clock`
  installs, says so in the review, and does not run — which is the honest answer until P1
  gives a parent a way to grant it.

`code` is a **different field** and is still never executed, on any install, ever.

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

## `source_version` and the imported overlay

Every record carries **`source_version`** (default 1) — the *author's* counter for that one
item. It is what makes an upgrade distinguishable from a re-import: bump it when you change a
prompt you have already shared, and every appliance that has the old one is offered an
upgrade rather than a silent replace. The engine itself never reads it.

The files here are the **shipped defaults**. What actually runs is *defaults ⊕ the imported
overlay*, keyed `kind:key` (conversation = `module_id/content_id`, global and schedule =
`name`) and stored in `$MOXIE_DATA_DIR/fleet/content_items.json`. A parent installs a
[content pack](../../docs/architecture/content-module-contract.md#content-packs-moving-content-between-machines-p0-built-2026-09-02)
over the top from the console's 📦 card; because these records carry a version, our own
release upgrading `starter.json` obeys exactly the rule a stranger's pack does — and an item
the parent edited here comes back as a conflict rather than being taken back. Editing a file
here still works and still wins on a fresh appliance; on one that has imported a pack, the
overlay wins for the keys it names.

Add an activity by dropping another `.json` here and pointing `MOXIE_CONTENT_MODULE` at it
(or a directory to merge — a future loader slice), or by exporting one from the 📦 card and
sending somebody the file.
