# 🛠️ Docs bundle + guards — publish and verify

Rebuilds the static docs-explorer bundle and runs every doc and site guard, keeping the Moxie doc tree consistent top to bottom. Use after editing anything under docs/ or the explorer (sim/web/docs.html), before committing.

Invoke it by name (`publishing-moxie-docs`) — it is a **shared agent skill**, so any agent working in this repo can load it instead of re-deriving the method.

| File | Purpose |
|---|---|
| [`SKILL.md`](SKILL.md) | The skill itself — instructions the agent follows. |

## What it covers

- Rebuild + verify (in order — all must pass before committing)
- The standing rules the guards enforce
- Gotchas
- Completeness bar

---
📖 [Skills](../README.md) · [Back to top](../../../README.md)
