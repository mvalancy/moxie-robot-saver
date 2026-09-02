# 🎛️ Orchestration plan — delivering the top-level Moxie outcomes

**Mode (from 2026-09-02):** an orchestrator session plans, briefs, and integrates; **Opus agents do the
implementation**, one bounded task each, in isolated git worktrees. This doc is the plan those agents and
the layered session loops work from. The technical spec of *what* we're building stays in
[`implementation-plan.md`](implementation-plan.md) (Definition of done) and the six contracts.

## The three top-level outcomes

| # | Outcome | Measured by |
|--:|---|---|
| 1 | **Full cloud service** — a child talks to Moxie end to end; data-driven content; cloud management (console + config/telemetry); interchangeable SIM/robot clients; one-command stack; green + live-tested | the six criteria + % in the DoD table of [`implementation-plan.md`](implementation-plan.md) |
| 2 | **Scrape OpenMoxie for all of its best features** — [OpenMoxie](https://github.com/jbeghtol/openmoxie) (MIT, jbeghtol) is the canonical revival; we credit it and port what's better than ours | the ADOPT list in [`openmoxie-feature-audit.md`](openmoxie-feature-audit.md), burned down |
| 3 | **Take it 10 levels beyond** — the "ghost in the shell": any AI becomes Moxie; a platform, not a patch (see [`vision.md`](vision.md), [`moxie-as-a-platform.md`](moxie-as-a-platform.md)) | the BEYOND list in the audit + our own roadmap, shipped behind the same contracts + tests |

OpenMoxie is **MIT and explicitly in scope to study**. The clean-room rule is unchanged for the *vendor
Android app* (never read it or its decompiled output) — it does **not** apply to OpenMoxie.

## How work happens

```
orchestrator: read plan → cut the next slice → brief an Opus agent → review → integrate → verify CI → update plan
agent:        own worktree on feat/<slice> off origin/dev → build + test → commit locally → report (never push)
integration:  push feat branch → PR into dev (fast CI) → merge → standing dev→main PR (deep CI) → promote + tag (RELEASING.md)
```

**Every agent brief carries the same protocol** (copy it verbatim):
- **Isolation:** `git worktree add /home/scubasonar/Code/moxie-robot/wt-<slice> -b feat/<slice> origin/dev`; work only there; commit; do not push; do not touch `main`/`dev`.
- **Clean-room:** build from `docs/architecture/` + `docs/reverse-engineering/`; the vendor app is forbidden; OpenMoxie is allowed + credited.
- **Secrets:** never print/commit keys; `mqtt/.env` stays untracked; staged diff must contain no `sk-…`.
- **Attribution:** commit messages end with the `Co-Authored-By` + `Claude-Session` lines the orchestrator provides.
- **Quality gates:** a test for every feature; hermetic suite green (`python -m pytest sim/tests -q -k "not test_sil and not test_docs" --ignore=sim/tests/test_live_gateway.py`); doc guards (`build_docs_bundle` + `check-doc-links` + `check-doc-consistency` + `node sim/test_docs.mjs`); SIL smoke on a **free** port when the runtime changed (`MOXIE_SIL_PORT=19xx bash sim/run_smoke.sh`); never kill processes you didn't start.
- **Report:** branch, commits, what shipped, tests/counts, guard + smoke results, live observations, honest gaps — under 400 words.

**Integration rules (orchestrator):** review the diff before pushing; one PR per slice into `dev`; merge only
green; resolve doc-index/README link conflicts in one place; after a `dev→main` squash-promotion,
reconcile `dev` (see RELEASING.md "After a promotion"); resolve the standing PR number with
`scripts/standing-pr.sh`.

## Workstreams (the backlog agents draw from)

- **WS-A · Cloud service (outcome 1).** The DoD, slice by slice: the remaining criterion-1 finishers
  (browser Web-Audio playback of `CloudTTSResponse`; a live talk-through with real speech), criterion 3's
  last gap (telemetry/insights view — *in flight*), criterion 5 (one-command stack verified end to end),
  criterion 6 (a full live e2e scenario). Source of truth: the DoD table.
- **WS-B · OpenMoxie ADOPT (outcome 2).** Burn down the audit's ranked ADOPT list, best-first. Each item:
  port the *behavior* (never their code verbatim without attribution), behind our contracts, with tests.
- **WS-C · BEYOND (outcome 3).** The audit's BEYOND list + our vision: any-AI brain transplant behind the
  RemoteChat seam; a content-authoring studio for data-driven modules; multi-robot fleet management;
  local-first privacy with a LoggingPolicy that's actually enforced; voice cloning / multi-voice via
  Piper + gateway; a real insights layer. Each ships as a contract change + implementation + test.
- **WS-D · Platform & release.** Packaging (`moxie-cloud-sdk`), the three CI tiers, semver releases,
  `docker compose up`, docs that a newcomer can follow.

## The layered session loops (24/7 continuity)

Session-scheduled loops keep the project moving while the operator is away; when a session hits a usage
limit, queued fires resume as the limit resets. They are **session-scoped and auto-expire after 7 days**
— re-arm them at the start of each session/week.

| Tier | Cadence | Each fire (orchestrator, delegating) |
|---|---|---|
| **BUILD** | 30 min | pick the next WS-A/WS-B slice from this plan + the DoD → brief an Opus agent → integrate → CI green → update plan |
| **INTEGRATION** | 1 h | brief an agent to exercise the whole stack + live infra, chase real e2e gaps, harden the harness → integrate |
| **AUDIT** | 2 h | DoD scoring, spec conformance, guards/CI, secrets, contradictions, loop self-improvement, release promotion |
| **RESEARCH** | 3 h | WS-B/WS-C grooming: refresh the OpenMoxie audit vs upstream + forks, rank the next ADOPT/BEYOND items, draft agent briefs |

Rules that keep it safe unattended: smallest shippable slice; don't manufacture work; verify before commit;
honesty over green; idempotent + interruptible; one thing at a time, don't stomp (see the
`running-layered-session-loops` skill).

## Status log (append-only, absolute dates)

- **2026-09-02** — Orchestrator mode begins. v0.1.0 released (cloud-server foundation, M1–M5). In flight:
  `feat/openmoxie-audit` (the feature audit doc) and `feat/m6-insights` (telemetry/insights view, the last
  M6 gap). DoD ≈ criterion 1 ~70%, 2 🟢, 3 ~85%, 4 🟢, 5 🟡, 6 ~70%.

- **2026-09-02** — Integrated `feat/m6-insights` (PR #3 → dev, squash `ecdaff6`): telemetry/insights view
  — the last M6 gap. **M6 🟢, DoD criterion 3 🟢, DoD ≈ 57%.** Opus agent built it in an isolated
  worktree (+13 tests, 105→118; SIL smoke incl. TTS; live-verified through a real broker); the
  orchestrator merged forward onto dev (regenerated the docs bundle to resolve the only conflict), opened
  the PR, waited for green, merged. Honest gaps recorded: telemetry is in-memory (last 50/robot), typed
  `event_data` decoding deferred. Still in flight: `feat/openmoxie-audit`.

---
📖 [Implementation plan](implementation-plan.md) · [Vision](vision.md) · [Releasing](../../RELEASING.md) · [Docs index](../README.md)
