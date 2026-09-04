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
| [`brain-picker.md`](brain-picker.md) | **BEYOND #3** — any brain, hot-swappable, per child: a closed positive registry of brains, per-robot selection layered `defaults ⊕ fleet ⊕ per-robot` like every other config value, a live swap with no restart, and an explicit `MOXIE_APP` that **pins** the appliance's brain | S/M | 🟢 **P0 SHIPPED 2026-09-03** — [`brains.py`](../../../mqtt/moxie_sdk/brains.py) behind [`test_brains.py`](../../../sim/tests/test_brains.py) (82) + [`test_brain_runtime.py`](../../../sim/tests/test_brain_runtime.py) (31) + [`test_brain_console.py`](../../../sim/tests/test_brain_console.py) (13), plus a 22-guard mutation run. Ships with the 🧠 console card. **P1 open:** the *persona* half of the binding, per-child keys/cost accounting (all three need a new secret) |
| [`sandboxed-extensions.md`](sandboxed-extensions.md) | **BEYOND #6** — sandboxed content extensions: a pack that can *do* something (count, check the clock, remember a score) without trusting its author. A declarative rule list over a total JSON-AST expression language — no `exec`, no parser, no loops, no reachable host object — behind a declared capability set the parent reads in plain English at import review | S/M · M · L | 🟢 **P0 SHIPPED 2026-09-03** — [`ext.py`](../../../mqtt/moxie_sdk/content/ext.py) behind [`test_ext_escapes.py`](../../../sim/tests/test_ext_escapes.py) (X1–X12) and [`test_ext.py`](../../../sim/tests/test_ext.py) (T1–T18), 150 tests, plus a 28-guard mutation run. All six upstream hooks hand-ported as the golden set; G1 ships as a real activity. **P1 open:** `act`/`subscribe` (needs the `RemoteChatAction` wire, brief S5), `brain`, `turn.after`/`session.end`, the text surface, the JS evaluator in `workerd`, the console card. **P2 open:** a Wasm runtime, publisher signatures |
| [`production-hardening.md`](production-hardening.md) | **§4.4 #3** — production hardening: the cross-process store story (a decision ADOPT #8 deferred three times), MQTT reconnection, and *"stays connected for a week"* as a test we can run without hardware | M · M · L | 🟢 **P0 + P1 shipped 2026-09-03; P2 build-ready.** §3 **decided** the owed question — advisory `flock` on a per-record sidecar behind a public `JsonStore.transaction()`, JSON staying on disk — over WAL-SQLite and over a single-writer rule, and named what would flip it; neither build hit a falsifier. §4's reconnection shipped in P0 (including the trap that `connect_async` without `retry_first_connection=True` changes nothing). §5's soak shipped in P1 and **has run**: `sim/run_soak.sh`, three profiles, twelve numeric bars printed pass-or-fail — 1 046 turns with 0 lost while up, reconnect p95 0.62 s, 0 lost updates across 4 contending processes. Plus the durable roster, the connection telemetry stream, and the SIGTERM handler. The **12th** bar is the interesting one: it exists because a whole defect class (a cached belief about the robot outliving the robot's state — the roster ghost, the vision latch) sat underneath eleven green ones. **Six of its twenty-four assumptions need a physical robot** — the honest ceiling on this whole area, unmoved by either phase |
| [`content-authoring.md`](content-authoring.md) | **§4.4 #6** — content authoring, the verb packs did not ship: a parent can install a stranger's conversation and diff it against their own, and still cannot compose one without editing JSON. Where authoring lives (four options, one choice), what a non-programmer may and may not write, the edit → hear → keep loop priced in gateway calls, and one validation path rather than two | S/M · M · L | 🟢 **build-ready 2026-09-03** — the spec **removed all four owed decisions** (§11 checks itself against them): authoring lives in the **parent console**, as a second verb on the 📦 card, because it is the only surface that already holds the content store, the validation path *and* the rehearsal hook. The write seam was pre-declared in code — [`packs.mark_edited`](../../../mqtt/moxie_sdk/content/packs.py)'s docstring names *"a future 📦 edit button"* — and the review's 2×2 needs **no new state**, because `is_local_edited` already treats an item with no `imported_rev` as edited, so a later pack reports `CONFLICT` for free. The only new safety code in the whole brief is **one `if`**: `mark_edited` normalizes but does not validate, so the save route must call `validate_item` itself exactly as `apply_pack` does. The loop is five rungs and **exactly one spends a gateway call** — typing, the resolved-prompt panel and rehearsing an opener are all free; a *try* is one brain call per click, budgeted and counted on screen; trying a **command** costs nothing at all. **Three of its twelve assumptions need a real parent** (A9–A11) and one needs a robot (A8); none blocks P0 |
| [`community-signals.md`](community-signals.md) | **Not an audit item — the inbound half.** What owners holding a real Moxie report, cited by URL and date, ranked by evidence strength rather than appeal | — | 📡 **Scan 1, 2026-09-03.** Eight findings. One (**C1**, the CereProc licence) strengthens a blocked row in the audit; one (**C3**, IP drift after pairing) is a new build-ready gap on no other list; four are documentation corrections. **r/MoxieRobot was unreachable** — the scan's largest gap, named in its §4 |

> **Why this table is dated.** On 2026-09-03 two build agents were briefed off stale status markers and
> each lost a full run to work that had already merged. Four of the seven rows above said *"ready to
> build"* for something already shipped. **When a brief ships, flip its state here and in the audit in
> the same PR** — and prefer naming the test over naming the PR, because a test is checkable.

> **The last row is a different kind of page.** Every other brief turns a ranked line into something an
> agent can execute. `community-signals.md` does the opposite: it brings evidence *in* from outside this
> repo, so its rows are findings with URLs rather than designs with tests. It ranks by how good the
> evidence is, and it says plainly where a community problem is one we have already solved — which is a
> marketing finding, not an engineering one.

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
