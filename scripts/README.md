# 🛠️ scripts

Repo-maintenance helpers — the mechanical doc guards. Run from the repo root before committing docs
(see the [docs-tree SOP](../docs/README.md#-how-this-documentation-tree-is-maintained-sop)).

- `check-doc-links.py` — verify every internal markdown link **and `#anchor` fragment** resolves, so
  the docs stay navigable. `python3 scripts/check-doc-links.py`
- `check-doc-consistency.py` — the documentation-tree consistency guard: flags **stale claims** (things
  we've since disproven, asserted as live) and enforces the robot-side **firmware version stamp**.
  `python3 scripts/check-doc-consistency.py`
- `check-mermaid.mjs` — validate every ` ```mermaid ` block renders (needs `npm i mermaid jsdom`).
  `node scripts/check-mermaid.mjs` (the simulator's `sim/test_mermaid.mjs` is the CI-wired equivalent).
