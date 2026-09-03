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

| Brief | Audit items | Effort | State — live, 2026-09-03 |
|---|---|:--:|---|
| [`expressiveness.md`](expressiveness.md) | **ADOPT #3** — the markup floor (§1) · **BEYOND #1** — the behavior planner (§2) | M → L | §1 🟢 shipped 2026-09-02 · §2 🟢 **build-ready** (audit §4.4 **#4**) |
| [`security-broker-auth.md`](security-broker-auth.md) | **§3.1 Robot identity / JWT** — broker ACL (P0), device credentials (P1), spoof-proofing (P2) | M · M · S | P0 🟢 **shipped 2026-09-02 (PR #44)** — `test_broker_acl.py` (30) + `run_acl_proof.sh`. P1/P2 ⛔ **blocked on A1–A4**, which need a physical robot (audit §4.4 #5) |
| [`telehealth.md`](telehealth.md) | **ADOPT #7** — puppet / telehealth: the command path and the "Be Moxie" console panel | M | 🟢 **shipped 2026-09-02 (PR #43)** — `test_telehealth*.py`, 110 tests |
| [`content-packs.md`](content-packs.md) | **ADOPT #5** — content packs: a versioned, digest-checked pack file; export from a positive field allowlist; import-with-review whose per-item state tracks `source_version` **and** local edits, so an upstream re-import never clobbers | S/M · S/M | 🟢 **shipped 2026-09-02 (PR #51) — P0 *and* P1**; hardened 2026-09-03 (PR #78). 160 tests |
| [`voice-picker.md`](voice-picker.md) | 🎚️ Speech + Listening dropdowns in the console — pick from the gateway's real audio models and the installed local engines; default `piper-amy` / `stt-whisper`; explicit local always wins | S/M | 🟢 **shipped 2026-09-02 (PR #48)**; corrected 2026-09-03 (PR #77 — an explicit `MOXIE_TTS`/`MOXIE_STT` **pins** the engine, so *"explicit local always wins"* is enforced, not merely intended) |
| [`live-sim-demo.md`](live-sim-demo.md) | 🌐 **The headline goal** — the hosted Moxie Sim alive on a static edge: three same-origin Cloudflare Pages Functions (brain · voice · ears) behind hard caps, demo-mode only, degrading to the pre-cached scripted Moxie when the gateway is unconfigured, over budget, at capacity or down | M | 🟡 **P0-a + P0-b shipped 2026-09-02 (PR #54, #61); the ears (PR #66) and the fallback voice (PR #69) 2026-09-03.** The rest of P1 is 🟢 **build-ready** and ranked **#1** in the audit. This page keeps its own state current — read its §10 ledger, not this cell |
| [`brain-picker.md`](brain-picker.md) | **BEYOND #3** — any brain, hot-swappable, per child: a closed positive registry of brains, per-robot selection layered `defaults ⊕ fleet ⊕ per-robot` like every other config value, a live swap with no restart, and an explicit `MOXIE_APP` that **pins** the appliance's brain | S/M | 🟢 **P0 SHIPPED 2026-09-03** — [`brains.py`](../../../mqtt/moxie_sdk/brains.py) behind [`test_brains.py`](../../../sim/tests/test_brains.py) (82) + [`test_brain_runtime.py`](../../../sim/tests/test_brain_runtime.py) (31), plus a 22-guard mutation run. **P1 open:** the 🧠 console card, per-child persona |
| [`sandboxed-extensions.md`](sandboxed-extensions.md) | **BEYOND #6** — sandboxed content extensions: a pack that can *do* something (count, check the clock, remember a score) without trusting its author. A declarative rule list over a total JSON-AST expression language — no `exec`, no parser, no loops, no reachable host object — behind a declared capability set the parent reads in plain English at import review | S/M · M · L | 🟢 **P0 SHIPPED 2026-09-03** — [`ext.py`](../../../mqtt/moxie_sdk/content/ext.py) behind [`test_ext_escapes.py`](../../../sim/tests/test_ext_escapes.py) (X1–X12) and [`test_ext.py`](../../../sim/tests/test_ext.py) (T1–T18), 150 tests, plus a 28-guard mutation run. All six upstream hooks hand-ported as the golden set; G1 ships as a real activity. **P1 open:** `act`/`subscribe` (needs the `RemoteChatAction` wire, brief S5), `brain`, `turn.after`/`session.end`, the text surface, the JS evaluator in `workerd`, the console card. **P2 open:** a Wasm runtime, publisher signatures |

> **Why this table is dated.** On 2026-09-03 two build agents were briefed off stale status markers and
> each lost a full run to work that had already merged. Four of the seven rows above said *"ready to
> build"* for something already shipped. **When a brief ships, flip its state here and in the audit in
> the same PR** — and prefer naming the test over naming the PR, because a test is checkable.

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
