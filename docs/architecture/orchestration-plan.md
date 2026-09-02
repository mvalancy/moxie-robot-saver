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

- **WS-A · Cloud service (outcome 1).** The DoD, slice by slice: the remaining criterion-1 finisher
  (a live talk-through with real speech — *in flight*; browser Web-Audio playback landed in PR #11), criterion 3's
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

## Integration playbook (learned 2026-09-02, day one — apply every time)

1. **Merge forward before PR.** An agent's branch base predates whatever landed while it worked; in its
   worktree: `git fetch && git merge origin/dev --no-edit`, then verify guards + the hermetic suite on the
   *merged* tree before pushing.
2. **Generated docs bundle conflicts are resolved by regenerating, never by hand.** Every integration
   so far conflicted only on `sim/web/docs-{bundle,index.json,search.json}`: `git checkout --theirs` them,
   `python3 sim/tools/build_docs_bundle.py`, `git add`. A non-bundle conflict is a stop-and-look.
3. **Concurrent agents edit different rows/cells of `implementation-plan.md`.** Brief each agent to
   touch only its own DoD row / status row / one gap bullet. When two rows conflict, reconcile row-wise
   (take each agent's newer row). The orchestrator owns the table header / % / "next slice" line.
4. **Reserved-file lists in every brief** name exactly which files the other in-flight agents own; check
   `git diff --name-only` against that list at review time.
5. **Bounded commands.** Scout with targeted `ls`/`grep`, never `find /`; keep polls ≤ ~100 s per call
   and make merge/cleanup chains idempotent (re-runnable after a timeout).
6. **Cleanup is verified, not assumed.** After a squash-merge: `git worktree remove … --force`,
   `git worktree prune`, delete the local branch, and check the remote — `gh pr merge --delete-branch`
   did not reliably remove remote `feat/*` branches; delete any whose PR is MERGED.
7. **Honest gaps are the backlog.** Every agent report's "gaps" paragraph goes into the plan's status
   log verbatim-ish; the RESEARCH/BUILD loops draw the next slices from there, not from guesses.

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

- **2026-09-02** — Integrated `feat/openmoxie-audit` (PR #4 → dev): [`openmoxie-feature-audit.md`](openmoxie-feature-audit.md)
  — every OpenMoxie (MIT) + fork feature classified HAVE / ADOPT / BEYOND, with ranked **Top-10 ADOPT** and
  **Top-10 BEYOND** lists. This is now the WS-B / WS-C backlog. It also surfaced a real conformance bug —
  `_on_activity` answers `schedule`/`mentor_behaviors` with the wrong wire shape (no `request_id` echo,
  generic `result` key) — delegated immediately as `feat/activity-wire-shape` (in flight).

- **2026-09-02** — Integrated `feat/activity-wire-shape` (PR #5 → dev): the `query_result` envelope is now
  spec-conformant (`request_id` echoed; payload keyed `schedule` / `mentor_behaviors` per the recovered
  `CloudQueryResponse` proto; OpenMoxie corroborates). +13 tests (118→131). Content is still empty — the
  audit's ADOPT #1 (schedule serving + generative day plan) and #2 (`mentor_behaviors` ingest/serve) are
  the next WS-B slices; they are what turns "connects" into "works" on a real robot. In flight:
  `feat/llm-action-tags` (ADOPT #4).

- **2026-09-02** — Integrated `feat/llm-action-tags` (PR #6 → dev, ADOPT #4): the brain can now steer the
  robot with `<exit>` / `<sleep>` / `<launch:MOD[:CID]>` tags parsed out of its own text into real
  `Reply.actions` (both LLM and content apps); malformed own-tags are always stripped so a child never
  hears one. +29 tests (131→160). Caveats recorded: `launch_if_confirmed` is lossy (→ LAUNCH) until
  `ActionType` gains a confirm member; live model compliance is prompt work. In flight:
  `feat/schedule-mentor-behaviors` (ADOPT #1+#2).

- **2026-09-02** — Integrated `feat/schedule-mentor-behaviors` (PR #7 → dev, ADOPT #1+#2): a real robot now
  receives a real day plan (`ContentSchedule` built from our content modules' `schedules[]` + the 23-module
  catalog) and its `mentor_behaviors` are ingested + served, so sessions start, missions don't repeat, and
  FTUE completes — "connects" → "works". Durable JSON store (`store.py`, stepping stone to a DB). +41 tests
  (160→201). Live-verified: 12-entry plan; a reported behavior round-trips; `WELCOME` retires. Gaps: plan is
  deterministic (BEYOND #7 adaptive), store is JSON (ADOPT #8), `response_code` not emitted. In flight:
  `feat/one-command-stack` (M7), `feat/live-e2e-validation` (criterion 6).

- **2026-09-02** — Integrated `feat/one-command-stack` (PR #8 → dev, M7 + ADOPT #10): `docker compose up`
  runs certs → broker → supervisor → console with healthchecks + volumes; `sim/run_compose_smoke.sh` PROVES
  a virtual robot round-trips (incl. TTS audio) through the composed stack and the console sees it. **M7 🟢,
  DoD criterion 5 🟢.** +14 tests (201→215). Also fixed Piper ≥1.3 producing no audio. A `compose-stack`
  deep-CI job was added (template + `.github` synced; active on `main` after the next promotion). Gaps:
  `stt` profile unverified live; profiles need `MOXIE_SUPERVISOR_EXTRAS` + `--build`. In flight:
  `feat/live-e2e-validation` (criterion 6) — then promote **v0.2.0**.

- **2026-09-02** — Integrated `feat/live-e2e-validation` (PR #9 → dev): today's merges are now **live-proven**
  against the real gateway — action tags 0/3 → 3/3 (prompt-only; the tag goes *before* the sentence), the
  shipped content module through the real runtime, and a 9-test console↔runtime round-trip with a
  runtime-key-diffed double. **DoD criterion 6 → ~80%.** Real speech + one full talk-e2e scenario remain
  the honest gap. Shared `sim/tests/helpers_runtime.py`. No agents in flight; all six delegated slices
  landed today (PRs #3–#9). **Next: promote dev → main as v0.2.0.**

- **2026-09-02** — **Released v0.2.0** (dev → main squash `7e2fc35`, tag `v0.2.0`, release tier green, wheel +
  sdist published). Standing PR recreated as #10; dev reconciled with a zero-content merge. Day one of
  orchestrator mode: six delegated slices (PRs #3–#9), hermetic suite 105 → 224, DoD ≈ 1 ~70% · 2 🟢 · 3 🟢 ·
  4 🟢 · 5 🟢 · 6 ~80%. Next (per implementation-plan): browser Web-Audio playback of `CloudTTSResponse`
  (criterion 1's last client-side link), then a live talk-through with real speech.

- **2026-09-02 (AUDIT)** — Guards green, bundle 0-diff, standing PR #10 CLEAN with all six checks (the new
  `compose-stack` deep job is live on `main` after v0.2.0). Package builds 0.2.0. No keys in tracked files or
  history; `.env` files ignored; workflow token works; four loop tiers armed. Spec check (config-and-telemetry):
  `LoggingPolicy` 0/1/2 and `RobotCloudConfig` field names match the contract; **gap filed:** `alarms`
  (`WakeSchedule`) and `schedule_preferences` (`ParentRequest`) are contract fields the builder/sanitizer
  don't emit yet → a WS-A slice. **Contradiction:** the DoD header still reads ≈57% while its rows are
  1 ~70% · 2–5 🟢 · 6 ~80% (4/6 🟢; simple mean ≈ 90%, but "done" means all six 🟢) — header fixed at the
  next merge (both in-flight agents edit that table). Hygiene: removed six merged remote `feat/*` branches;
  playbook above codified. In flight: `feat/web-sim-audio-playback`, `feat/live-talk-e2e`.

- **2026-09-02** — Integrated `feat/web-sim-audio-playback` (PR #11 → dev): the browser SIM decodes the wire
  itself and **plays the server's `CloudTTSResponse`** (chunk queue, autoplay handling, mute, mouth animation
  from marks/envelope); live-observed 4.0 s of cloud TTS in the browser with zero console errors. New
  `sim/test_audio.mjs` (encoder-parity + mutation-checked) in the fast tier; +3 Playwright tests. Criterion 1's
  last client-side link is done; what remains is real speech (`feat/live-talk-e2e`, in flight).

- **2026-09-02** — Integrated `feat/live-talk-e2e` (PR #12 → dev): **real speech is live-proven end to end
  through the real runtime** — Piper "child" audio → zmqSTT protobuf frames → whisper → turn → spec
  `RemoteChatResponse` → Piper Amy `CloudTTSResponse` → whisper re-hears it (overlap 1.00), with 0 gateway
  calls (a global) and with a real completion. Anti-tone guard makes the placeholder voice fail it. A degraded
  gateway now skips rather than false-greens. Also fixed a latent env-dependent test. **Findings filed as
  slices:** (1) **the brain is the bottleneck** — 45 s healthy / 18 s degraded vs the robot's ~20 s reprompt
  window, voice legs ≈1.5 s → background inference + filler (audit, Fork A pattern) is now the top WS-B item;
  (2) Piper reads emoji aloud → `strip_markup` should drop them (S); (3) live tests read `mqtt/.env` from their
  own tree → skip in worktrees (harness S). No agents in flight.

---
📖 [Implementation plan](implementation-plan.md) · [Vision](vision.md) · [Releasing](../../RELEASING.md) · [Docs index](../README.md)
