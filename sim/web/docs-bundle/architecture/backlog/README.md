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
| [`expressiveness.md`](expressiveness.md) | **ADOPT #3** — the markup floor (§1) · **BEYOND #1** — the behavior planner (§2) | M → L | §1 shipped · §2 ready to build |
| [`security-broker-auth.md`](security-broker-auth.md) | **§3.1 Robot identity / JWT** — broker ACL (P0), device credentials (P1), spoof-proofing (P2) | M · M · S | ready to build |
| [`telehealth.md`](telehealth.md) | **ADOPT #7** — puppet / telehealth: the command path and the "Be Moxie" console panel | M | ready to build |
| [`content-packs.md`](content-packs.md) | **ADOPT #5** — content packs: a versioned, digest-checked pack file; export from a positive field allowlist; import-with-review whose per-item state tracks `source_version` **and** local edits, so an upstream re-import never clobbers | S/M · S/M | **P0** (headless: pure module + store + five routes + a live reload) ready to build; **P1** the 📦 console card |
| [`voice-picker.md`](voice-picker.md) | 🎚️ Speech + Listening dropdowns in the console — pick from the gateway's real audio models and the installed local engines; default `piper-amy` / `stt-whisper`; explicit local always wins | build-ready (2026-09-02); after the STT + telehealth slices land |
| [`live-sim-demo.md`](live-sim-demo.md) | 🌐 **The headline goal** — the hosted Moxie Sim alive on a static edge: three same-origin Cloudflare Pages Functions (brain · voice · ears) behind hard caps, demo-mode only, degrading to the pre-cached scripted Moxie when the gateway is unconfigured, over budget, at capacity or down | build-ready (2026-09-02); P0 is one sitting |
| [`sandboxed-extensions.md`](sandboxed-extensions.md) | **BEYOND #6** — sandboxed content extensions: a pack that can *do* something (count, check the clock, remember a score) without trusting its author. A declarative rule list over a total JSON-AST expression language — no `exec`, no parser, no loops, no reachable host object — behind a declared capability set the parent reads in plain English at import review | S/M · M · L | build-ready (2026-09-02); **P0 is one sitting**, and its first commit is a security fix `dev` needs anyway (§2.6) |

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
