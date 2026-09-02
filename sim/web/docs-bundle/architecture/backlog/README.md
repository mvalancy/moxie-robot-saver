# 🗂️ Backlog briefs — a ranked line, turned into something a build agent can execute

The [OpenMoxie feature audit](../openmoxie-feature-audit.md) ranks *what* to build (§4.1 Top-10 ADOPT,
§4.2 Top-10 BEYOND) and tracks *what has shipped* (the **Status** column in both tables). A ranked line is
a decision, though — not a plan. When an item's next step is big enough that a build agent would otherwise
have to re-derive the design, it gets a **brief** here.

## What a brief contains

Every page in this folder answers the same questions, in this order, so an agent can start at the top and
work down:

1. **Goal** — one paragraph, and what "done" looks like from the child's side.
2. **The seam** — the exact file, function and call sites it plugs into today, and what that costs.
3. **The vocabularies** — the recovered ids it may emit, each cited to a doc **and line range** in
   [`../../reverse-engineering/`](../../reverse-engineering/README.md). Nothing outside them reaches the wire.
4. **Prior art** — what [OpenMoxie](https://github.com/jbeghtol/openmoxie) (MIT) does, cited by path, and
   which **behaviors** we port. We describe and credit; we never copy code.
5. **The design** — pure and testable where it can be, with the invariants written down.
6. **Tests** — a numbered table, hermetic first.
7. **Acceptance criteria** — checkable, not aspirational.
8. **Effort, files to touch, risks** — including the limits we could *not* establish from our own docs.

## The briefs

| Brief | Audit items | Effort | State |
|---|---|:--:|---|
| [`expressiveness.md`](expressiveness.md) | **ADOPT #3** — the markup floor (§1) · **BEYOND #1** — the behavior planner (§2) | M → L | ready to build |

## House rules

- **Clean-room.** Derive from our own [reverse-engineering](../../reverse-engineering/README.md) and
  [architecture](../README.md) docs. OpenMoxie and its forks may be read and cited by path; never copied.
- **Honesty over completeness.** A brief says what it could not establish — an unverified robot behavior, a
  bundle-defined namespace, a missing hardware capture — in its own risks section, not in a footnote.
- **Cite by line.** `doc.md` lines 115–128 beats "see the markup doc": a reader must be able to check a
  claim in one jump.
- **Status lives in the audit.** When a brief ships, flip the audit's Status column in the same PR — one
  place to look, so the two never disagree.

---
📖 [Docs index](../../README.md) · [Architecture index](../README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) · [Implementation plan](../implementation-plan.md)
