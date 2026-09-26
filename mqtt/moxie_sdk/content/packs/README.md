# 📦 Content packs

One file you can email, review before it installs, and undo
([brief](../../../../docs/architecture/backlog/content-packs.md) ·
[authoring](../../../../docs/architecture/backlog/content-authoring.md)). Pure: stdlib only,
no store, no HTTP, clock only via an injected `now`. The supervisor owns the `JsonStore`
collections and routes (`mqtt/supervisor/moxie_runtime/content.py`). Callers import
`moxie_sdk.content.packs` only.

| Module | Holds |
|---|---|
| [`__init__.py`](__init__.py) | The file format, the load-bearing decisions, and the public surface |
| [`items.py`](items.py) | Format constants, the positive per-kind field allowlist (`SPEC`), canonical digests, item normalize/key/validate |
| [`wire.py`](wire.py) | `export_pack` / `parse_pack`: items to a pack file and back, refusing anything unreadable |
| [`review.py`](review.py) | The 2×2 review (version × edited-here) that never clobbers a local edit, and `apply_pack` |
| [`authoring.py`](authoring.py) | Phrase lists ⇄ patterns, and the shadow check for a new command |
| [`overlay.py`](overlay.py) | Shipped defaults ⊕ installed items → one `ContentModule`, and the inventory |

A pack is data and stays data: `test_content_pack_sandbox.py` asserts no file here reaches
for the renderer, `eval`/`exec`/`compile` or an import hook. Mutation rows in
`sim/tools/ext_mutation_check.py` (X12) and `authoring_mutation_check.py` anchor on lines here.

---
📖 [Content engine](../README.md) · [Back to top](../../../../README.md)
