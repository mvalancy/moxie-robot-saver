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

### 🎯 The headline goal (owner, 2026-09-02 evening) — outcome 1's public face

**A live, hosted Moxie Sim that is actually alive.** `moxie.mattvalancy.com` (Cloudflare Pages, static)
runs the full SIL loop with the full cloud experience — a real brain, real TTS and real STT through an
OpenAI-compatible gateway — as a **demo-mode** experience: a visitor gets the whole thing and can nuke
nothing. Non-negotiables, in the owner's words and our reading of them:

| Requirement | What it means here |
|---|---|
| Real AI, real voice, real ears | brain + `/audio/speech` + `/audio/transcriptions` reach a live gateway |
| **Reasonable token limits** | per-request `max_tokens`, per-visitor and global ceilings — the demo can never run up a bill |
| **Cloudflare must not hammer our server** | per-IP and global rate limits at the edge, with an honest 429/503 |
| A **Cloudflare-locked token** for the site | the key lives in a Cloudflare secret binding and is used only by a same-origin Function; the browser never sees it, and the origin is pinned |
| **Demo mode** | no destructive or stateful writes are reachable from the public page |
| **Fallback to the old Sim mode** | gateway offline / over budget / at capacity → pre-scripted, pre-cached speech + ambient self-talk, with an honest indicator |
| **Capacity indicator** | when too many visitors are on, the page says so plainly instead of failing |
| **The repo is public** | *any* Moxie sim and *any* OpenAI-compatible gateway must work by configuration — no hostname, key or account id is hard-coded; Cloudflare/GitHub secrets carry the rest |

Spec in progress at `backlog/live-sim-demo.md`. Nothing about this goal may hard-code
`gateway.graphlings.net` or `moxie.mattvalancy.com`: those are deployment config.

### Model policy (owner, 2026-09-02)

**Opus 5 does most of the work; Fable 5.1 is for planning and the genuinely hard parts.** Implementation
slices go to Opus agents; contract-level specs, tricky debugging and architecture calls may use Fable.

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
8. **Run the suite creds-free unless you mean to go live.** Live tests now find `mqtt/.env` from any
   worktree, so a plain full-suite run spends gateway calls. Agents run the hermetic suite as
   `MOXIE_LLM_API_KEY= .venv/bin/python -m pytest …` and make live calls only in an explicit, budgeted step
   (state the cap in the brief; ≤ 6 is the default).
9. **The fast tier runs the WHOLE suite with the fast tier's deps.** A test that constructs a class which
   talks to a real service must inject a fake through a `client=`-style seam (never rely on `importorskip`
   to hide a hard import), and the tiers' hermetic test deps stay in parity (`sim/ci/ci.yml` ↔ `ci-deep.yml`).
   Verify a fix in a *fast-shaped* venv (no optional deps) before pushing.
10. **Concurrency by disjoint file sets, not by turns.** A second slice may run while one is in flight
    only when its files are provably disjoint: the brief carries the in-flight agent's files as a
    RESERVED list, plan edits stay on distinct rows, and no two agents touch the same runtime region
    (turn/streaming loop + safety gates, `_push_config`, the status HTTP handlers). Cap two BUILD slices
    plus one INTEGRATION/RESEARCH task. Same-file-different-region edits merge cleanly; same-region
    edits don't — plan the split before launching, not at merge time.
11. **Browser tests assert recorded state, never live samples.** Three fast-tier flakes came from one test
    family sampling a live value inside a short window (`#tts-status` text, `getMouthOpen()`, `ttsPending()`)
    on a loaded runner. The page records what happened (peak, order, counts, per event) and the test waits
    for completion, then asserts the record. Briefs for any SIM/Playwright work must say so explicitly.
12. **Verify `MERGED` before any cleanup.** `gh pr merge` can be rejected (a conflict that appeared when a
    sibling PR merged first). Check the PR state is `MERGED` before removing the worktree or deleting the
    branch — deleting an open PR's head branch closes the PR. Recovery, if it happens: `git fetch origin
    refs/pull/<n>/head`, re-branch, merge forward, open a new PR. (Happened once: PR #37 → #38.)
13. **Gate every push on the guards' success lines.** `tail -1` of a guard is not a verdict — capture the
    line and require its ✅ before committing (`check-doc-links` prints a broken-link list above its tail;
    `test_docs.mjs` warns about README orphans). Two red docs pushes on 2026-09-02 were the orchestrator's.
14. **Promote freely, tag rarely.** dev → main promotion is the end-to-end exercise (the deep gate builds
    the package, runs compose + HIL, builds the images multi-arch without pushing). A `v*` tag publishes a
    Release + three GHCR versions — cut one only on the owner's word or at a plan-named milestone, and mark
    pre-1.0 tags pre-release. Owner rule 2026-09-02; v0.1.0–v0.7.0 relabelled pre-release.
15. **Rate-limit resilience.** (a) Agents commit work-in-progress locally after every completed step, so a
    429 kill loses at most one step; a killed agent is resumed by message (its worktree and transcript
    survive), not relaunched. (b) Loop fires that queue up during a limited window arrive together —
    treat the batch as ONE fire: integrate what finished, resume what died, launch at most the normal cap.
    (c) After each squash-merge, confirm the **dev push** fast run is green (it can differ from the
    PR-side run); a red dev is fixed before the next merge. (d) Pace concurrency to the budget: two Opus
    agents at a time is the ceiling while a limit is fresh.
16. **The merge gate is `scripts/pr-green.sh`, not a grep over `gh pr checks`.** It reads the PR's
    `statusCheckRollup` and passes only when every check is COMPLETED + SUCCESS, at least three checks
    exist, and the SIL job is among them. Why: on 2026-09-02 PRs #43–#48 merged about two minutes after
    opening while the six-minute SIL job was still running — the old gate read a queued/missing job or
    an empty API reply as "no non-pass lines". Dev's own push runs caught the resulting red; rule 15(c)
    stays as the second line of defence.
17. **A guard must assert over code, not over the whole file.** The fast tier's audio test asserted
    `!src.includes("moxie_sdk")` across all of `bridge.js` to enforce client/server independence, so
    *citing* where a wire shape came from — the house style everywhere else here — failed a test about
    imports. It now ignores comment lines, and it was verified in both directions (an injected
    `require("moxie_sdk")` still fails). When a guard fires, decide whether the code or the guard is
    wrong before changing either, and say which in the commit.
18. **A row-wise reconcile must be verified, not trusted.** When both sides changed the same table row,
    the reconciler cannot know which is newer — it guessed, and silently reverted the durable-telemetry
    status while integrating the research branch. After any row-wise merge of a shared table, diff the
    affected rows against `origin/dev` and restore dev's wherever the branch carried an older base.
    Caught within minutes on 2026-09-02 only because the row was re-read; assume it will happen again.
19. **The preview build is a test you are not writing.** Cloudflare Pages builds every PR, and on
    2026-09-03 it caught what 1 637 green hermetic tests could not: `import … with { type: "json" }`
    in a Function. Node accepts that attribute, the Pages bundler does not, so no runtime we test on
    could have seen it. When an external check fails while ours pass, that asymmetry **is** the
    finding — read it before assuming flake. The fix also added a local guard, which is the general
    move: turn a deploy-only failure into a local one.
    **Second instance, same shape (2026-09-03):** `globalThis.navigator = {…}` passed on this
    machine's Node 20 and threw on CI's Node 24, because Node 21 made `globalThis.navigator` a
    getter-only accessor. Generalised: *a feature cannot be validated by the runtime the tests
    happen to run on* — bundler, Node version, browser engine alike. Both times the durable fix
    was the same: convert the remote-only failure into a local guard.
20. **A git-ignored file in the main checkout can hide a whole class of test from CI.** `mqtt/.env` exists
    only there — never in a worktree, never in CI — and `config.py` loads it with `setdefault` at import.
    Tests that simulate "nothing configured" monkeypatch-delete a variable and reload the module, at which
    point the file **refills it**. Twelve tests across three files therefore assert nothing on any machine
    that has a real `.env`, while passing everywhere it is absent. Found 2026-09-03 by running the suite in
    the main checkout: 12 failed there, 3975 passed with the file moved aside. Lesson: when a suite behaves
    differently in the main checkout than in a worktree, the difference is the finding — and an agent that
    only ever works in a worktree structurally cannot see it.
    **Generalised 2026-09-03:** a rate limit is not the only way an agent dies mid-flight. Two agents
    were killed within moments by a transient **HTTP 529 Overloaded**, a server-side condition with no
    warning and no reset time to plan around. So (a) applies to *any* mid-flight termination, and the
    recovery is identical: resume by message — the worktree and transcript survive — and never
    relaunch, which would duplicate work. An agent killed before it creates its worktree simply starts
    over, which is why the per-step commit rule matters most in the first few minutes.

21. **"Only a real deploy can settle this" is usually false — open a PR and curl its preview.** Every
    branch push publishes a **public** Cloudflare Pages preview; the URL is in the PR's bot comment
    (`https://feat-<branch>.moxie-robot-saver.pages.dev`, plus a per-deployment hash alias). Three
    questions this repo had recorded for days as owner-blocked — including the live-Sim spec's
    *highest-risk* unknown — were answered in about two minutes on 2026-09-03 (§10 assumptions 8 and 9
    TRUE, new assumption 27 FALSE). One of the three was a genuine hole: `sim/web/_headers` does not
    apply to a Pages **Function** response, so the security block never covered the two routes that can
    spend money. Two corollaries worth keeping: a missing *route* answers **200 with the static HTML
    fallback, not 404**, so probe by content type rather than status; and a preview being keyless proves
    nothing about Production/Preview variable separation while Production is *also* keyless. Escalate to
    the owner only for things that genuinely need the dashboard — setting variables, reading plan
    limits, flipping package visibility.

22. **Never chain cleanup behind a merge in one command.** On 2026-09-03 I ran
    `gh pr merge 78 --squash --delete-branch` and the worktree/branch cleanup as one chained
    command. The merge **failed** — the PR had gone `CONFLICTING` when its sibling landed
    underneath it — but the chained cleanup ran anyway, deleted the branch, and GitHub
    **auto-closed the PR**. This is rule 12 (verify `MERGED` before any cleanup) violated by
    the person who wrote rule 12, and the reason is mechanical rather than forgetful: the
    cleanup was not *gated* on the merge, it merely *followed* it, and `gh pr merge` reports
    failure by printing a hint line rather than by anything a chained `;` notices. Recovery
    was complete — the head commit was still reachable through `refs/pull/<n>/head`, the
    branch was restored, the conflict resolved row-wise, and `gh pr reopen` brought the PR
    back — but it cost a CI cycle. **So: merge, read the state back (`gh pr view <n> --json
    state`), and only then clean up, in a separate command that runs only when the state is
    literally `MERGED`.** A loop over several PRs must `break` on the first that is not.

23. **A gate result is only true for the commit it was read on — re-read it in the same
    command that merges.** Made twice on 2026-09-03, once by machine and once by hand. The
    machine version: a merge watcher read `gate: all` from a run that had already been
    superseded by a push, and tried to merge; `gh` refused with `UNSTABLE`, so nothing
    landed unverified, and the watcher was fixed to pin the head SHA and re-check it had not
    moved. The hand version, an hour later and worse: I read the standing PR green at 10/10,
    then `dev` took two merges, deep CI restarted, and I merged the promotion on the older
    read — **the same mistake, in the one place where `gh` will not save you**, because a
    promotion into `main` is mergeable regardless of whether the deep tier has finished.
    What bypassed the gate was a test harness, a `ci-deep.yml` line and docs — no runtime
    code, each already through its own fast CI — and the next deep run went green and
    validated the changed gate retroactively. That is luck, not process.
    **So: read the gate and merge in the SAME command, gated on that read** — pin
    `headRefOid` first, re-read it after the gate check, and refuse if it moved. The
    generalisation, which is the reason this is a rule rather than a note: **any check whose
    subject can change between the check and the action is not a check, it is a memory.**
    That is the same defect as the roster ghost, the vision latch and the wakeup-into-a-dead-
    socket — a cached belief about a moving thing — and it is the most common bug this
    project has produced.

## The layered session loops (24/7 continuity)

Session-scheduled loops keep the project moving while the operator is away; when a session hits a usage
limit, queued fires resume as the limit resets. They are **session-scoped and auto-expire after 7 days**
— re-arm them at the start of each session/week.

| Tier | Cadence | Each fire (orchestrator, delegating) |
|---|---|---|
| **BUILD** | 30 min | pick the next WS-A/WS-B slice from this plan + the DoD → brief an Opus agent → integrate → CI green → update plan |
| **INTEGRATION** | 1 h | brief an agent to exercise the whole stack + live infra, chase real e2e gaps, harden the harness → integrate — promote when green; **do not tag** (rule 14) |
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

- **2026-09-02** — **Released v0.3.0** (dev → main squash `3cfb7d4`, tag `v0.3.0`, release tier green, wheel +
  sdist published). Standing PR recreated as #13; dev reconciled (zero-content merge). DoD rescored honestly:
  **4/6 🟢, criteria 1 and 6 at ~90%** (remaining: a physical Moxie in the loop; creds-gated live tests in CI).
  Eight delegated slices landed today (PRs #3–#9, #11, #12). Next slice delegated: **background inference +
  filler** for brain latency (`feat/brain-latency-filler`).

- **2026-09-02** — Integrated `feat/brain-latency-filler` (PR #14 → dev): a slow brain is no longer silence —
  past `MOXIE_BRAIN_BUDGET_S` (default 6 s) the runtime speaks a kid-appropriate filler as chunk 0
  (`REPLY_PENDING`), keeps inferring, and delivers the real line as chunk 1 (`is_completed=true`); stale-turn
  guard; both chunks synthesized. Live: filler at 3.0 s, reply at 17.9 s (was 17.9 s of silence). Also emoji-free
  TTS text and `.env` discovery from worktrees. +18 tests (→233). Recorded assumption: the physical robot's
  handling of chunk 0 is inferred from the fields, proven only on the SIM. Gaps → next: the filler fires once
  (a 45 s turn goes quiet at ~26 s) → re-arm + token streaming; playbook rule 8 added (creds-free suite runs).

- **2026-09-02** — Integrated `fix/cloud-tts-status-race` (PR #15 → dev): the fast-tier flake that reddened
  two dev pushes (the env probe's late "no TTS server" write clobbering the 🔊 speaking indicator) is fixed at
  the root — `audio.js` owns `#tts-status`, `env.js` routes through `setTtsHint()`, the Playwright test asserts
  one atomic snapshot then waits for teardown. 5× green locally; CI SIL green on the PR. In flight:
  `feat/streamed-reply-chunks`, `feat/ci-live-dispatch`.

- **2026-09-02** — Integrated `feat/ci-live-dispatch` (PR #16 → dev): the deep tier's manual dispatch now runs
  every creds-only live suite with a fail-loud gate, plus an opt-in `voice=true` tier (pinned, sha256-verified
  Piper voices + cached Whisper) that runs the real-speech talk loop — "live-tested" is reproducible on demand.
  actionlint clean; docs (sil-and-cicd "Live CI", RELEASING CI table). Proof dispatch run on dev after merge.
  In flight: `feat/streamed-reply-chunks`.
- **2026-09-02** — Integrated `feat/streamed-reply-chunks` (PR #17 → dev): the answer itself now streams.
  `MoxieApp.respond_stream` yields one `ReplyChunk` per finished sentence (pure segmenter in
  `moxie_sdk/segment.py`; `chat.stream_completion` opens `stream=True` through the same backoff/`Pacer`),
  and the runtime publishes each as `REPLY_PENDING` + `chunk_num`, closed by `SUCCESS` + `is_completed`.
  The filler timer re-arms per chunk (cap 2/turn), a newer turn cancels the stream mid-answer, and a
  one-chunk answer stays byte-identical to the old wire. Live: **first sentence at 1.52 s, whole answer at
  4.38 s** (4 chunks, one `event_id`) on a healthy gateway day. Both SIM clients now treat a turn as a
  turn, not a reply. +48 tests (→281). Knob: `MOXIE_STREAMING=0` restores the single-reply path. Recorded
  assumption (unchanged but leaned on harder): no capture proves a physical Moxie plays chunk 2.

- **2026-09-02 (orchestrator)** — **Live CI proven in GitHub Actions:** dispatch run 33622345297 on dev with
  `voice=true` → all four deep jobs green, i.e. the creds-only live suites AND the real-speech talk loop
  (piper/whisper installed on the runner, pinned voices fetched) passed under the fail-loud gates. Also:
  RELEASING.md's stale "workflows can't be pushed from the session" section replaced; playbook note — the
  generated *bundle copies* of docs conflict too, regenerate them (orchestrator's exclusion pattern fixed).
  Bumping to **0.4.0** for promotion (streaming + live CI + browser-audio fix + brain-latency filler).

- **2026-09-02** — **Released v0.4.0** (dev → main squash `ebeda0d`, tag `v0.4.0`, release tier green, wheel +
  sdist published). Standing PR recreated as #18; dev reconciled (zero-content merge). Since v0.3.0: streamed
  sentence chunks (first sentence at first-token latency), the slow-brain filler, live CI proven on GitHub
  Actions (10 + 4 live tests passed on the runner incl. real speech), the browser-audio flake fixed at the
  root. Hermetic suite 281. Three releases today (v0.2.0 → v0.4.0); 12 delegated slices integrated (PRs
  #3–#9, #11, #12, #14–#17). No agents in flight.

- **2026-09-02 (RESEARCH)** — Integrated `feat/backlog-expressiveness` (PR #19 → dev): the audit now carries
  honest status marks per item (PR numbers; JSON store ≠ database; `TTSMark[]` still empty), and
  `docs/architecture/backlog/expressiveness.md` holds a build-ready brief for ADOPT #3 (the automarkup floor —
  pure deterministic `annotate()` behind the unchanged seam, 0 unknown asset ids, one mood per streamed
  answer, byte-exact goldens) plus a phased contract spec for BEYOND #1 (the behavior planner). Caveats from
  our recovered docs: no gaze verb among the 24 markup commands; only two confirmed SFX ids; no physical
  Moxie has played our markup. This is BUILD's next slice after safety. In flight: `feat/input-safety-contract`.

- **2026-09-02** — Integrated `feat/input-safety-contract` (PR #20 → dev, BEYOND #2): child safety is an
  enforced contract — `InputSafety` on the wire (proto-cited), a transparent local rule engine behind a
  `Classifier` seam, gates at input / per streamed chunk / whole reply, a parent review queue + 🛡️ console
  panel. Live: an unsafe request cost 0 gateway calls and the child heard a caring redirect. +84 tests
  (→361 hermetic creds-free). Honest limits recorded (regex floor; a spoken chunk can't be unsaid). Audit
  rows reconciled with the concurrent research branch. Next: ADOPT #3, the automarkup floor (brief in
  `backlog/expressiveness.md`).

- **2026-09-02** — Integrated `fix/fast-tier-openai-and-mouth-peak` (PR #21 → dev): the fast tier is green
  again. Root causes: `LLMApp.__init__` hard-imported `openai` even with an injected fake (fast tier installs
  fewer deps than deep and runs the whole suite), and a Playwright test sampled the mouth live in a 5 s window.
  Now a `client=` seam, tier dependency parity in `ci.yml`, and a recorded mouth peak (0 muted / ~1.0 rendering).
  412 pass in both venv shapes. Playbook rule 9 added. In flight: `feat/automarkup-floor`.

- **2026-09-02 (AUDIT)** — All green: guards + 0-diff bundle, standing PR #18 CLEAN (six checks), fast tier green
  on dev again, package 0.4.0 builds and the wheel ships `safety_rules.json`, no secrets in tree/history,
  `.env`/data dirs ignored, token works, zero stale branches, CI templates in parity, four loops armed.
  Spec check (content-module contract): `set_output`/`add_execution_action`/`local_data` exist, but
  **`persist_data` and `summarize` are listed on the volley/session API and are not implemented** → WS-A
  slice (it unlocks the MemoryChat pattern; audit ADOPT "session.summarize() + persist_data"). Contradiction
  filed: the plan's Current-state "MQTT runtime" row still reads 🟡 "core works" after streaming, filler,
  safety gates, schedule serving, telemetry and config editing landed — fix at the next merge (two agents
  edit that file now). Self-improvement: playbook rule 10 + the BUILD loop now allow a second provably
  disjoint slice (the last three concurrent pairs merged cleanly). In flight: `feat/automarkup-floor`,
  `feat/config-alarms-fleet-defaults`. Release v0.5.0 queued behind the automarkup floor.

- **2026-09-02** — Integrated `feat/automarkup-floor` (PR #22 → dev, ADOPT #3): every line Moxie speaks now
  performs — mood + gestures from our recovered vocabularies (11 moods / 12 gestures / 50 trees / 52 spurts,
  cited by line), a deterministic blake2b-gated `annotate()` behind the unchanged seam, 8/8 goldens, 0 unknown
  ids, p95 0.23 ms, +280 tests (→641), and the browser SIM rendering six distinct faces with arms moving.
  Honest gaps: SFX/icons/spurts gated off (thin catalogs); the model's own mood reaches only the closing
  chunk on a streamed turn until `ReplyChunk` carries scored fields (planner phase); no hardware has played
  it. In flight: `feat/config-alarms-fleet-defaults`. **Next: promote v0.5.0** ("safe and alive").

- **2026-09-02** — **Released v0.5.0 "safe and alive"** (dev → main squash `f0d1663`, tag `v0.5.0`, release tier
  green, wheel + sdist published). Standing PR recreated as #23; dev reconciled (zero-content merge). Over
  v0.4.0: the enforced child-safety contract, the automarkup floor, the fast tier fixed at the root, the audit
  with per-item status + an expressiveness backlog. Hermetic suite 281 → 641. Four releases today
  (v0.2.0 → v0.5.0), 16 delegated slices integrated. In flight: `feat/config-alarms-fleet-defaults`;
  delegating `feat/session-memory-persist-summarize` (contract gap: `persist_data` + `summarize`).

- **2026-09-02** — Integrated `feat/config-alarms-fleet-defaults` (PR #24 → dev): the config contract's `alarms`
  (`WakeSchedule`) and `schedule_preferences` (`ParentRequest`) are built + editable, and a fleet-level default
  config layers under per-robot overrides (ADOPT #6). Encodings the protos don't pin (day numbering, HH:MM,
  epoch seconds) are isolated behind single constants and recorded as assumptions. +31 tests (→666 in the
  fast-shaped venv). Live: two robots inherited a fleet alarm while a per-robot volume override won. Next
  disjoint slice: device allowlist / pairing gate. In flight: `feat/session-memory-persist-summarize`.

- **2026-09-02 (orchestrator)** — Fast tier flaked again on `8b7fb0a`/`9470e6c` (~50%): the chunked cloud-TTS
  Playwright test sampled `ttsPending()` live and fast runners had already drained the queue. Same disease as
  PR #15/#21, same cure — `fix/cloud-tts-queue-stats` (in flight, disjoint files) records per-playback stats
  and asserts them after completion. Playbook rule 11 added. In flight: `feat/session-memory-persist-summarize`,
  `feat/device-allowlist`, `fix/cloud-tts-queue-stats`.

- **2026-09-02** — Integrated `feat/session-memory-persist-summarize` (PR #25 → dev): the content contract's
  `persist_data` + `session.summarize` are built as a structured, provenance-carrying, parent-erasable memory
  floor (BEYOND #4 floor) — `LoggingPolicy` honored from the effective config, module-declared memory, safety +
  verbatim filters, `on_session_end` hook, `GET/DELETE/POST /memory`, `memory_chat.json` shipped. +42 tests
  (→712). Live: Moxie greeted a returning child with "I remember you have a beagle named Pepper!". Honest gaps
  → backlog: parent memory UI in the console; summaries are sticky and can be wrong (an invented pronoun);
  no decay/per-item edit; summarization holds a pool thread. In flight: `feat/device-allowlist`,
  `fix/cloud-tts-queue-stats`.

- **2026-09-02** — Integrated `fix/cloud-tts-queue-stats` (PR #26 → dev): the chunked cloud-TTS test asserts
  recorded playback stats (`lastPlaybackStats()`: chunks played, order, max queue depth — seeded at the
  speaking edge) after completion instead of sampling the queue live; a synchronous fast-drain case makes the
  assertion structural. Third and last member of this flake family; playbook rule 11 governs. In flight:
  `feat/device-allowlist`; delegating `feat/published-images` (ADOPT #10 remainder).

- **2026-09-02 (orchestrator)** — Rule 11 paid off immediately: with recorded stats in place, the fast tier
  on `ce0e0c6` reported `order: [0, 2, 1]` — a **real** ordering bug in the browser SIM's chunk queue (chunk 2
  can start before chunk 1 in a burst; identical code passed on `bd00268`, so it's a race in the queue, not
  the observer). `fix/cloud-tts-chunk-order` in flight (allowlist: audio.js/bridge.js + tests): ordering
  becomes a property of the design — dequeue strictly by expected `chunk_num` with a documented gap rule.
  Until it lands, dev's fast tier is intermittently red for that one assertion. In flight:
  `feat/device-allowlist`, `feat/published-images`, `fix/cloud-tts-chunk-order`.

- **2026-09-02** — Integrated `feat/device-allowlist` (PR #27 → dev): the pairing gate is **closed by default** —
  an unpermitted robot is pending, gets an un-paired config with no `child_pii`, and no brain; permitting it
  (console 🔐 card, one click; auto-permit on the console pairing path) re-pushes the full config at once.
  Gate on the transport boundary; `"unpairing"` recorded as an assumption behind one constant. +34 tests
  (→765 full / 739 fast-shaped); smoke, scenarios, compose smoke green; two-robot live proof. Honest gap:
  service refusal, not authentication — broker ACL/JWT still deferred. In flight: `feat/published-images`,
  `fix/cloud-tts-chunk-order`.

- **2026-09-02** — Integrated `feat/published-images` (PR #28 → dev, ADOPT #10): the release tier now publishes
  multi-arch (amd64 + arm64) images to GHCR on every `v*` tag (`supervisor`, `console`, `broker-certs`; the
  broker is upstream mosquitto), and a self-contained `docker-compose.images.yml` gives owners the
  two-command install with no clone. Proven locally (real OCI manifest lists; images-mode smoke green;
  drift guard on the inlined broker config); actionlint clean. **First real publish happens at the next tag
  — and the packages start private (one-time flip to public on the repo's Packages page).** In flight:
  `fix/cloud-tts-chunk-order`, `feat/parent-memory-browser`.

- **2026-09-02** — Integrated `fix/cloud-tts-chunk-order` (PR #30 → dev): the real `[0, 2, 1]` bug is fixed at the
  root — ordering was a property of the *queue* (what's still waiting); with short chunks and one message per
  round trip, chunk 0 drained before chunk 1 landed and chunk 2 started alone. Now the *player* enforces it
  (n+1 only after n; 1.2 s gap rule; late chunks dropped; 5 s event window), with tests that fail
  deterministically on the old code. Fast tier should be steadily green again.
- **2026-09-02** — Integrated `feat/parent-memory-browser` (PR #29 → dev): 🧠 "What Moxie remembers" in the
  console — facts/preferences/open threads per activity with provenance, erase per activity or everything,
  empty + policy states; the double's honesty guard covers the memory handlers. +19 tests (→753 fast /
  784 full). Gaps: no per-item erase/edit (runtime has none), `summarized_through` needs a one-line runtime
  change, nothing decays. BEYOND #4 stays 🟡 until decay/edit.

- **2026-09-02 (orchestrator)** — v0.6.0 promotion paused by a red deep gate: since PR #28 the prebuilt-image
  compose smoke failed because `docker-compose.images.yml` (built in parallel with PR #27) never forwarded the
  now-closed pairing gate's `MOXIE_ALLOW_UNVERIFIED_BOTS`; the robot got `pairing_status='unpairing'`. Fixed in
  `fix/images-compose-gate-env` (PR #31 → dev, one-line passthrough; images-mode smoke green locally).
  Playbook lesson (rule 10 corollary): two concurrent slices that both touch *deploy config* need an
  integration-time smoke of the *combined* tree, not just each branch's own — the orchestrator now runs the
  images-mode compose smoke at merge whenever compose/deploy files changed. Promotion resumes on a green gate.

- **2026-09-02** — **Released v0.6.0 "a real appliance"** (dev → main squash `9ca5c95`, tag `v0.6.0`). Standing PR
  recreated as #32; dev reconciled (zero-content merge). Over v0.5.0: the config contract completed + fleet
  defaults, the memory floor + parent browser, the closed-by-default pairing gate, published multi-arch images
  (this tag is the first real GHCR publish — the packages start **private**; the owner flips them public
  once), the chunk-order fix. Six releases today (v0.2.0 → v0.6.0); 23 delegated slices integrated. In flight:
  `feat/vision-events`, `feat/memory-item-erase`.

- **2026-09-02** — **First real image publish succeeded:** the v0.6.0 release run built + pushed all three
  multi-arch images (supervisor, console, broker-certs; amd64 + arm64) to GHCR alongside the sdist/wheel —
  the two-command install is now real. Owner action outstanding: flip the three packages **public** on the
  repo's Packages page (no API for it). "Pending first tag" wording retired where found.

- **2026-09-02** — Integrated `feat/memory-item-erase` (PR #33 → dev): a parent can erase or **correct** any single
  remembered line — stable derived item ids (migration-free), per-item provenance, a use-clock decay with pinning,
  edits re-checked for safety and verbatim child speech (unsafe correction → 400). +23 tests (→807 full / 773
  fast-shaped). **Audit BEYOND #4 → 🟢.** In flight: `feat/vision-events`, `test/compose-parity-guards`.

- **2026-09-02 (INTEGRATION)** — Integrated `test/compose-parity-guards` (PR #34 → dev): hermetic PyYAML guards keep
  the clone and prebuilt compose files in sync — env passthrough + defaults per service, the inlined broker
  config diffed (plus a `$$`-escaping check the runtime smoke couldn't see), service/healthcheck/port/volume
  parity, doc parity; 20 negative cases prove each guard names the key and file. The v0.6.0 pause would have
  been caught in 0.2 s on the feature PR. `test_compose.py` 14→47; suite → 806. In flight: `feat/vision-events`,
  `feat/face-customization`.

- **2026-09-02 (AUDIT)** — All green after v0.6.0: guards + 0-diff bundle, standing PR #32 MERGEABLE (no-push
  multi-arch builds + docs + package pass; HIL/compose/SIL running), fast tier green on the last pushes, package
  0.6.0 builds, no secrets in tree/history, `.env` files ignored, token works, zero stale branches, all three
  workflow templates in parity with `.github/`, four loops armed. Spec check (ai-seam §2): `InputSafety`
  fields `is_unsafe`/`blocked_by`/`intents`/`phrase_id` present in `safety.py` + `wire.py` as specified. DoD
  unchanged at 4/6 🟢 (criteria 1 + 6 ~90%; the physical robot is the ceiling). RESEARCH delegated build-ready
  briefs for broker authentication (the permits gap) and puppet/telehealth (ADOPT #7). In flight:
  `feat/vision-events`, `feat/face-customization`, `feat/backlog-security-telehealth`.

- **2026-09-02** — Integrated `feat/vision-events` (PR #35 → dev, BEYOND #9): Moxie notices you walked in. Key
  finding: the vision events are not topics — they arrive as the `speech` of a `RemoteChatRequest` only after
  the brain sends `EventSubscription`, which is why nobody had ever seen one; we now subscribe, keep a
  hysteresis-filtered presence model, put presence in the turn context, and greet a returning child on the
  arrival event's own `event_id` (no unsolicited publish — recorded assumption). +70 tests (→820 fast /
  875 full); live: "There you are, friend!" with TTS at 5.04 s, rate-limited. Merge combined with PR #33's
  decay clock in `content_app.py`. In flight: `feat/face-customization`, `feat/backlog-security-telehealth`.

- **2026-09-02** — Integrated `feat/face-customization` (PR #36 → dev, ADOPT #9): a child can style Moxie's face —
  `ChildDecrypted.face_options` (field 17, clear) carries the choice; the catalog is honestly 12 cited options
  (two of 14 slots), zero invented ids, parent-supplied layers allowed; a deterministic cache-buster id
  (field-proven, not capture-proven). +42 tests (→849 full / 810 fast). Live: face_options + id on the wire.
- **2026-09-02 (RESEARCH)** — Integrated `feat/backlog-security-telehealth` (PR #37 → conflict with #36 → re-opened and merged as **PR #38**): build-ready briefs for
  broker authentication (P0 containment via pattern ACLs, no robot change; P1 JWT verification against enrolled
  keys; P2 spoof refusal at CONNECT — decisive finding: `ServiceConfiguration2` carries no broker credential, so
  nothing reaches a stock robot by QR) and puppet/telehealth (ADOPT #7). **Gateway TTS went live** (`piper-amy`,
  `piper-ryan`) — `feat/gateway-tts-live` delegated to wire + prove it. STT on the gateway still WIP.
- **2026-09-02** — Integrated `feat/adaptive-schedule` (PR #39 → dev): the fixed rotation behind `schedule` is now a deterministic, explainable recommender (`plan_inputs`/`plan_day`; parent request → FTUE → coverage → recency → affinity → time-of-day by recovered `ModuleCategory` → spread → blake2b tiebreak; never into bedtime), each entry with a plain-English *why* line on `GET /schedule`. +45 tests (`test_schedule_planner.py`). Filed honestly: telemetry `Packet` carries no module signal (finish/abandon from `mentor_behaviors` only), the 10-min slot is ours, times resolve in the server's timezone. **Gap → next:** a console card for the *why* lines (`server/` was reserved by the face slice). Audit BEYOND #7 → 🟢.
- **2026-09-02** — Integrated `feat/gateway-tts-live` (PR #40 → dev): the Graphlings gateway TTS (live today: `piper-amy`/`piper-ryan`, WAV 22050) is the default cloud voice behind `MOXIE_VOICE_BASE_URL` — `MOXIE_VOICE_MODEL`/`FORMAT`/`SAMPLE_RATE` knobs, RIFF-sniffed rate, latched fallback gateway → Piper → tone. Live proof: piper-amy → whisper overlap 1.00, 1.7 s latency; unknown model → tone in 0.38 s. +16 hermetic tests + creds-gated `test_live_gateway_tts.py` in the deep dispatch tier (repo secret `MOXIE_VOICE_BASE_URL` set). **Gateway STT still WIP** (user will say). Both compose files forward the new env (parity guard green). Next: v0.7.0 promotion; telehealth (ADOPT #7) + broker-auth P0 builds.
- **2026-09-02** — **Promoted v0.7.0**: standing PR #32 squash-merged with the deep gate all green (10/10) → main `1e427bc`, tag `v0.7.0` pushed (release workflow green (sdist/wheel + 3 GHCR images)); new standing PR #41; dev reconciled content-free (`3242654`). In this release: vision events in the turn loop (#35), face customization (#36), memory erase/edit/decay (#33), hermetic parity guards (#34), two build-ready briefs (#38), the adaptive explainable schedule (#39), the gateway voice with latched fallback (#40). In flight: telehealth (ADOPT #7), broker-auth P0, integration live-validation of the RC.
- **2026-09-02** — Gateway **STT verified live** (`stt-whisper` 3.4 s, `graphling-stt` 4.4 s, word-for-word on a 6 s clip); `feat/gateway-stt-live` launched (transcriber behind the seam, `MOXIE_STT=auto|gateway|whisper|off`, latched fallback, live proof, `litellm-stt-setup.md`; compose env forwarding deferred to integration because broker-auth owns the compose files). User rules recorded: local engines stay first-class; and a **console voice picker** (Speech + Listening dropdowns, default `piper-amy`) — brief written at `backlog/voice-picker.md`, queued behind the STT + telehealth slices. In flight: telehealth, broker-auth P0, integration v0.7, gateway STT.
- **2026-09-02** — Integrated `feat/integration-v07-live` (PR #42 → dev): the v0.7.0 RC validated through the real built backend — `test_schedule_sil_e2e.py` (13: the schedule the robot got == the schedule the parent is shown, bedtime/pin/FTUE/quit/feedback), `test_live_gateway_turn_e2e.py` + `helpers_stack.py` (one real turn on the assembled appliance: gateway brain → gateway voice, 22050 Hz real speech), `test_package_contents.py` (6), shared runtime/test helpers. Fold-ins: live turn e2e in the deep dispatch tier, package-data covers `apps/`+`content/` JSON, scenarios derive a status port. Fast-shaped 989 / 10. Lesson (rule 13): a docs push gates on the guards' ✅ lines, not on their tail — two red doc pushes today were mine.
- **2026-09-02** — **Owner rule: no per-promotion releases.** Promotions continue as the end-to-end exercise; tags only on the owner's word or a named milestone (playbook rule 14; RELEASING.md "Release cadence"). The seven existing releases (v0.1.0–v0.7.0) were relabelled pre-release; nothing deleted. Also folded: the loop-tier table's INTEGRATION row now says "promote when green; do not tag".
- **2026-09-02** — Integrated `feat/telehealth` (PR #43 → dev): **ADOPT #7 built** — pure `telehealth.py` from the recovered proto, six runtime verbs + activity branch + `GET/POST /telehealth`, operator text safety-checked as `role=MOXIE` with blocks returned (400 + reason), the 🎭 *Be Moxie* console card, SIM handler + `virtual_moxie.py --telehealth` + `run_smoke.sh --telehealth`. SIL proof enable→start→speak→interrupt→end; it caught and fixed a double-`END_SESSION` bug. +109 tests → fast-shaped 1098 / 11. Gaps: hardware questions B1–B5 stand (field-proven via OpenMoxie); owner guide deferred. Next: voice picker (after the STT slice lands), broker-auth P0 in flight.
- **2026-09-02** — Integrated `feat/broker-auth-p0` (PR #44 → dev): **broker hardening P0** — `%c` pattern ACLs (two files: console listeners vs the robot listener, because a listener with no password file accepts any username unchecked), a minted per-appliance supervisor credential (`gen-passwd.sh`, `MOXIE_MQTT_USER/PASSWORD/PASSWORD_FILE`, `_build_client`), 1883 loopback-bound by default (`MOXIE_BIND_HOST_PLAIN`), `broker_acl.render_acl` inert until P1. Proven against the real `eclipse-mosquitto:2.0.20` (`sim/run_acl_proof.sh`, 18/18); compose smoke green in build + images mode. +40 tests → fast-shaped 1138 / 11. Gaps in the ledger: 9001 keeps an anonymous fleet read (browser SIM), bare-metal conf needs one `gen-passwd.sh` run, P1/P2 not started.
- **2026-09-02** — Integrated `feat/gateway-stt-live` (PR #45 → dev): **gateway STT live** — `OpenAITranscriber` + `FallbackTranscriber` behind the Transcriber seam, `MOXIE_STT=auto|gateway|whisper|off`, per-engine model default (`stt-whisper`), URL/key fallback voice → LLM; pure `audio_models.py` (voices/ears classifier, defaults `piper-amy`/`stt-whisper`) as picker groundwork; `litellm-stt-setup.md` with a deployment matrix. Live: native + 16 kHz paths overlap 1.00; a real turn heard → answered → spoke through the gateway end to end. Fold-ins: compose forwards the STT knobs; **`MOXIE_TTS=piper|gateway` is now an explicit override** (owner rule: local engines stay first-class); both live suites in the deep dispatch tier. +37 tests → fast-shaped 1167 / 12. Next: the voice picker (brief `backlog/voice-picker.md`) once the schedule card lands.
- **2026-09-02** — Integrated `feat/schedule-why-card` (PR #46 → dev): the 📅 *Today's plan* console card — pure `normalize_schedule_view`, thin `GET /local/robots/{id}/schedule`, the *why* line per entry, a constraints footer (bedtime, pinned request, "finish/abandon comes from the robot's reports"). Live-proven through real broker + supervisor + virtual robot + console. +17 tests → fast-shaped 1184 / 12. Filed: the wire carries no `module_name` (card shows the id, the *why* line the label) — a small runtime follow-up. In flight: the voice picker.
- **2026-09-02 (after the 11:50 PT limit reset)** — **Session rate limit hit**: both BUILD agents (voice picker, face vocabulary) were killed mid-flight with uncommitted edits; ten loop fires queued during the limited window and arrived together — handled as ONE fire (rule 15). Agents resumed by message from their transcripts with a new WIP-commit rule. **Dev fast tier had been red since PR #43**: `test_telehealth.py::test_the_compiled_proto_agrees_with_the_recovered_text` imports a toolkit-generated, untracked `TeleHealth_pb2`; the SIL job installs protobuf so the `importorskip` passed and the import failed. Fixed on dev (`1ffc508`: skip when the oracle is not generated). Postmortem: the PR-side run and the dev-push run of the same content diverged in outcome, so my merge gate (PR checks all-pass) was not sufficient — rule 15 adds "confirm the dev push run after each squash". Integration + research agents deferred this fire to pace the session budget.
- **2026-09-02** — Integrated `feat/face-vocabulary` (PR #47 → dev): Moxie's look grows from 12 to **72 options** across 11 of the 14 recovered slots — OpenMoxie's asset table ingested as cited DATA (`face_assets.json`: commit hash, MIT notice, sha256 of the id list; ids only, no code; labels and slot mapping ours), loader with a `catalog=` seam, `face_child_id` pinned to recorded UUIDs, every manifest entry `caution: true` (nothing hardware-proven). +9 tests → creds-free 1248 / 11. Integration agent #2 launched (post-merges stack pass + the PR-vs-push CI divergence).
- **2026-09-02** — Integrated `feat/voice-picker` (PR #48 → dev): the 🎚️ **Voice** console card — Speech + Listening dropdowns from live gateway discovery (**31 voices** + 3 STT models found, no code change) + installed local engines + built-ins, defaults `piper-amy`/`stt-whisper`, explicit local wins, persisted in `fleet/voice.json`, swapped at runtime (`GET/POST /voice`, `POST /voice/test`), survives restart. Live: switched to piper-ryan → the SIL robot received 22050 Hz real speech; the switch-back caught a cold-start race (fixed + 5 tests). +111 tests → creds-free 1291 / 12. Fold-in: `MOXIE_VOICE_DISCOVERY_TTL_S` forwarded by both compose files. In flight: integration #2, research (audit re-rank + content-packs brief).
- **2026-09-02** — **Postmortem, corrected:** the integration agent proved the PR-side runs for #43–#48 were *never* green — each PR was squash-merged ~2 min after opening while its SIL job (5½–7 min) still ran; the old gate (`gh pr checks | grep -v pass`) treated a not-yet-listed job or an empty reply as green. dev has no branch protection, so nothing else stopped it. Fix: `scripts/pr-green.sh` (rollup-based, rule 16) is now the only merge gate; the fast workflow gained 24 guard tests (`test_ci_workflows.py`: event-symmetric, hermetic suite before the browser install). Earlier status-log lines that say "fast CI green" for #43–#46 were true only of the docs job; the content was validated by the dev push runs after `1ffc508`.
- **2026-09-02** — Integrated `feat/integration-2-post-merges` (PR #49 → dev, **the first PR merged through `scripts/pr-green.sh`**): CI event-symmetry guards (`test_ci_workflows.py`, 24), `Reply.actions`-to-robot e2e (5), telehealth-through-gateway-voice live test (4, creds-gated), shared `is_real_speech`, CI installs the console's own deps so its 55 acceptance tests actually run, scenarios wait on readiness. Verbatim: SIL ✅, telehealth SIL ✅, scenarios 2/2, ACL 18/18 on the real mosquitto image, live turn ears/brain/voice all gateway (overlap 1.00), package + wheel carry `face_assets.json`. Fast-shaped 1320 / 13. Gaps filed: no SIM client acts on `response_actions`; `bridge.js` publishes no activity log; `WebhookApp` doesn't strip own tags; brain latency 33.8 s on one live run.
- **2026-09-02** — Integrated `feat/research-content-packs` (PR #50 → dev): audit re-ranked after the day's merges (every row verified against code; upstream OpenMoxie + both forks checked at their live heads — **nothing new**). Three corrections filed: the console's MQTT `wakeup` publishes nothing yet reports success; telemetry is RAM-only (why the 📈 card cannot show a week); the runtime's `connect()` is blocking with no reconnect backoff. New top 5: content packs → durable telemetry/insights → sandboxed extensions → production hardening → any-brain-per-child. `backlog/content-packs.md` is build-ready. Also this window: a second session rate limit killed the content-packs BUILD agent before it created its worktree (relaunched, not resumed — nothing to preserve).
- **2026-09-02 (evening)** — **New headline goal recorded** (above): a live, demo-mode hosted Moxie Sim on Cloudflare with a real brain, voice and ears. The crux is stated once so no agent gets it wrong: the repo is public and the page is static, so the gateway key can live **only** in a same-origin Cloudflare Pages Function's secret binding — which is therefore the single choke point for token caps, per-IP + global rate limits, the model allowlist and origin pinning. A read-only survey (SIM front end · turn contract · Cloudflare deploy · fallback assets · abuse surface) is synthesising `backlog/live-sim-demo.md`. Two BUILD slices in flight: content packs P0, and **SIM robot fidelity** — the browser SIM consumes no `response_actions` and publishes no `client-service-activity-log` (the Python client does, `virtual_moxie.py`:195), so "SIM ↔ robot are identical clients" is currently overstated; that slice also fixes `WebhookApp` speaking its own markup. Model policy set: Opus 5 builds, Fable 5.1 plans.
- **2026-09-02 (late)** — Rate limit hit a third time; **the WIP-commit rule (15a) paid for itself** — both killed agents had every step committed, so nothing was lost and the orchestrator finished their tails. Integrated `feat/content-packs` (**PR #51**: pure pack engine with a digest, an allowlist pinned against `dataclasses.fields()`, the `source_version` + `local_rev` **2×2** review that refuses to clobber a local edit, undo-able apply, five routes, the 📦 console card; +110 tests → **1432 passed**; SIL smoke run by the orchestrator, which the agent never reached) and `feat/sim-robot-fidelity` (**PR #52**: the browser SIM now acts on `response_actions` and publishes `client-service-activity-log`, held from **both ends** against one golden so neither SIM client can drift; the only allowed delta is *which* robot and *when*; `WebhookApp` strips its own tags). The **live-Sim-demo spec** landed too (`backlog/live-sim-demo.md`, 880 lines, ten sections): caps with reasoning traced to our own measured latencies and byte sizes, a request-**unit** budget because no price sheet exists to denominate dollars, and the fail-safe default that unset config means degraded — never "guess our gateway".
- **2026-09-02 (late, cont.)** — **The new rollup gate earned its keep on its second day**: PR #52's SIL job came back **red**, which the old grep-based gate would have merged straight through. The cause was a false positive, not a bug in the slice: the audio test string-matched `moxie_sdk` over the whole of `bridge.js`, so an accurate citation in a *comment* failed a check about *imports* (`bridge.js` has no import machinery at all). Tightened to ignore comment lines and verified both ways — an injected `require("moxie_sdk")` still fails it — then #52 merged green. Playbook rule 17 records the general lesson.
- **2026-09-02 21:15 PDT — AUDIT** — All checks clean: **0** key hits in tracked files *and* in history (`git log --all -S`), `mqtt/.env` ignored (the one tracked `*.env` is `sim/compose-smoke.env`: ports and modes, no secrets), bundle 0-diff, links/consistency/docs-tests ✅, all three workflow templates byte-identical to their installed copies, no stale worktrees, no leftover `feat/*` branches local or remote, all four loop tiers armed, all 7 releases pre-release, wheel builds at 0.7.0 carrying `face_assets.json` + `safety_rules.json`, SIL smoke green. Conformance spot-check: `call_with_backoff` does honour a server `Retry-After` (`chat.py`:49‑53, 115) and `LoggingPolicy` maps 1:1 onto `data_sharing` (executed, all three values). **The audit moved a score DOWN:** DoD 4/6 → **3/6 🟢** — criterion 3 loses its green because telemetry is RAM-only (a restart erases the parent's history) and the console `wakeup` reports success while publishing nothing; criterion 4 keeps its green but is *earned* for the first time (PR #52). Launched **live-Sim P0-a** (the mode machine + honest indicator — the half that touches no secret at all); P0-b, the security-critical live turn, goes to Fable after the reset per the owner's model policy.
- **2026-09-02 21:25 PDT — BUILD** — Launched **durable telemetry + "the console stops lying"**, the audit's ranked #2 and the reason criterion 3 lost its green. Verified both claims first, and the second is **worse than it was filed**: not one endpoint but three — `wakeup` (`main.py`:289‑290), `reboot` (:293‑295) and `ota_status` (:299‑301) all report success or a hard-coded status while publishing nothing. The brief forbids inventing a command or a topic: each endpoint must either publish a *proven* recovered command or honestly report itself unsupported, and no test may assert a fake success. Telemetry gains store-backed persistence through the existing capped, atomic `store.append` (`store.py`:139) with daily rollups so "last week" is answerable without hoarding every packet — gated by `LoggingPolicy`, which is the one place in this slice where being wrong is a privacy incident rather than a bug, so all three policy values get a test. Two slices now in flight, both disjoint: live-Sim P0-a (`functions/**` + `sim/web`) and this one (`mqtt/**` + `server/**`).
- **2026-09-02 21:35 PDT — INTEGRATION** — **Promoted `dev` → `main` with no tag** (rule 14: promotion *is* the end-to-end exercise; a tag is a separate, owner-authorised act). The deep gate was green on the exact head (`f33d283`) across all seven jobs — package build, compose stack end to end, HIL, docs integrity, and all three images built multi-arch without pushing — so main is at `7c4eec3`, the standing PR is recreated as **#53**, and dev was reconciled with a **content-free** merge (verified empty). Still 7 tags, all pre-release; nothing published. **No integration agent this fire, deliberately:** two BUILD slices are mid-flight and both change the tree an integration pass would validate, so it would be measuring a snapshot that is about to be wrong twice — the next integration pass runs against the merged result. Free check done instead: the gateway is healthy and has **grown** to 49 models (31 TTS, 3 STT); `graphling-medium`, `piper-amy` and `stt-whisper` are all still present, which is what the live-Sim work depends on.
- **2026-09-02 21:50 PDT — RESEARCH (rotation c)** — Delegated the contract-level spec for **sandboxed content extensions** (BEYOND #6), which content packs landing has just promoted to *the* ceiling: a pack can now carry everything declarative and deliberately carries **no executable behaviour**, because we have no safe way to run someone else's code — so "we never `exec` anything" is what stands between shareable content and a real ecosystem. Upstream `exec()`s conversation `code` with a 10-second timeout, which is powerful and un-shareable. The brief makes the agent state the boundary first (extensions run in **our cloud**, never on the robot — our corpus establishes nothing about on-device code execution), weigh a self-interpreted DSL vs a declarative rule tree vs WASM vs a stripped scripting VM **including whether each can run inside a Cloudflare Worker** (the hosted demo needs it), design a capability model whose grants a non-programmer parent sees at pack-import review, and write the escape attempts as named tests — a sandbox is only as good as those. Docs only; reserved lists keep it clear of both in-flight BUILD slices.
- **2026-09-02 22:40 PDT** — Four merges, and one of them matters more than the rest. **PR #56 closed a live server-side template injection in our own code**: a content pack's `prompt` rendered through a plain `jinja2.Environment`, so an imported pack could execute code on the host — proven by execution before the fix (`os.getcwd()` returned the repo path; `__subclasses__()` enumerated 364 host classes). Packs became *shareable* one hour earlier in PR #51, which is what turned a latent smell into a delivery mechanism. The shipped container was safe only by accident (no jinja2 → the minimal renderer); every dev checkout and `.[content]` install was exposed. Fixed with a counting `SandboxedEnvironment` — and the measured behaviour corrected my own first comment: the sandbox does **not** raise, it substitutes undefined, so a hostile template is inert *and invisible*, hence the refusal counter. Eight escape probes pinned; 8 of the 14 new tests fail against the pre-fix renderer, verified by reverting. **Found by the research pass whose whole subject is "we never execute untrusted code"** (PR #57, `backlog/sandboxed-extensions.md`: a total JSON-AST expression language with zero escape surface, chosen because upstream's nine hooks turn out never to iterate). Also landed: **PR #54** live-Sim P0-a (the page asks `/api/health` what it can do instead of guessing from the hostname; five honest modes; no secret and no gateway URL ever reaches the browser; with nothing configured it is byte-identical to today) and **PR #55** durable telemetry + three console buttons that stopped lying (wakeup publishes the real recovered command; reboot returns 501 because no such cloud→robot command exists; firmware status never claims `up_to_date` again). Two of my own process errors this stretch: a PR body whose backticks the shell interpreted (use `--body-file`), and a row-wise reconcile that reverted a sibling's audit row → **playbook rule 18**.
- **2026-09-02 22:45 PDT — BUILD** — Launched **live-Sim P0-b, the live turn** — the security-critical half of the headline goal, on Opus per the owner's model policy (the reset is at 00:10, after which Fable is for the hard parts; a Fable security review of this slice is the plan once it lands). P0-a's contract is the base, and its honesty guard is deliberate: a fully configured deployment still renders `SCRIPTED` until `cloud-transport.js` sets `window.moxieCloudTransport`, so the LIVE badge cannot appear before a live turn actually works. The brief's spine is the four things that make a public proxy safe: the key never leaves the Function (asserted against every response, error paths included), the client's `model` field is **ignored rather than allowlisted** because ignoring cannot drift, HMAC tickets with a constant-time compare stop `/api/speech` becoming a free TTS endpoint, and every refusal is shaped so `mode.js` degrades instead of erroring. P0's counters are honestly best-effort in-process — a Worker isolate is not a shared counter, and exact counters are P1. Gateway budget 4 calls, spent proving the *request bodies the Function builds* are accepted by the real API, since Pages Functions do not run under a plain static server.
- **2026-09-02 22:50 PDT — INTEGRATION** — Launched the pass I deferred earlier, now that there is a merged state worth validating: four slices landed within the hour and none had been exercised **together** against real infrastructure. The brief names the specific risk each one carries rather than asking for a generic sweep, and the one that matters is the security fix: `render.py`'s new `SandboxedEnvironment` could quietly turn a legitimate content-module prompt into an empty string while every unit test still passed, degrading every content turn silently. So step 2 is to run the **shipped** modules through the real content app and assert each prompt still renders identically to the pre-sandbox output — and if a legitimate construct is broken, that is a bug in my own fix, to be fixed minimally with the sandbox kept. Also on the list: the telemetry hydration path and the `LoggingPolicy` gate under a *running* supervisor rather than a fixture, and the three changed console endpoints asserted with a real subscriber. Budget 6 gateway calls. P0-b's files are reserved; the "no CI tier runs these two node suites" gap is deliberately left to a guard test rather than a `ci.yml` edit, because P0-b owns that file.
- **2026-09-02 23:00 PDT — AUDIT** — **Promoted `dev` → `main` again, no tag** (main `4975c22`, standing PR recreated as **#58**, dev reconciled with a verified-empty content diff). All seven deep-gate jobs were green on the exact head. **Contradiction found and fixed:** the DoD header still read 3/6 while criterion 3's row had legitimately gone back to green with durable telemetry — header now **4/6 ≈ 91%**, and the note says plainly that this audit restored a score it had removed two hours earlier. **Conformance, by execution rather than reading:** the `LoggingPolicy` gate on the *new on-disk* telemetry path — `storable_packet` returns `None` under `NO_DATA` (nothing is written), withholds every `event_data` under the default `NO_MEDIA` behind an explicit marker, and keeps it only under `FULL`. That control only became load-bearing when telemetry started reaching disk an hour ago, which is why it was worth re-proving. Everything else clean: 0 key hits in tracked files and in history, bundle 0-diff, guards green, templates in sync, no leftover branches, 7/7 releases pre-release. Noted for integration: `.dev.vars` is not yet git-ignored — P0-b adds it, and I will verify rather than assume.
- **2026-09-02 23:20 PDT** — Integrated `feat/integration-3-merged` (**PR #59**): the merged state validated live, and the verdict I most wanted — **the sandbox fix breaks no legitimate prompt**, proven with the pre-fix renderer as a differential oracle (every shipped prompt byte-identical under two contexts, `BLOCKED` never moving, 27 constructs agreeing) *and* a **negative control** — a refuse-all sandbox changes every prompt, so the test can demonstrably detect the breakage it reports absent. Telemetry survived a real process restart with the robot never reconnecting; the policy gate was read **off disk** for all three values; `wakeup` was confirmed by a live MQTT subscriber. It also found two more suites no CI tier runs (the broker ACL proof and the telehealth SIL mode) and fenced all four behind a two-directional coverage ratchet whose lists can only shrink. 1785 passed / 15 skipped. **Then launched the production bug it filed:** the container ships **no jinja2**, so the fallback renderer passes `{% if %}` **verbatim into the brain's system prompt** while `content-module-contract.md`:42 advertises exactly that form — latent today (no shipped module uses a block) and broken for the first parent who authors or imports one. The fix is both halves: ship jinja2 in the container so the documented form works, *and* make the fallback strip what it cannot evaluate so nothing template-shaped ever reaches the brain. The sandbox and its parity test are explicitly off-limits.
- **2026-09-03 00:40 PDT** — **The hosted Sim can now hold a live conversation.** Merged **PR #61** (P0-b: `POST /api/chat` + `POST /api/speech`, HMAC tickets, the pre-inference safety floor, the in-process limiters, `cloud-transport.js`), **PR #62** (the container ships jinja2 *and* the fallback stops leaking template source into the brain's prompt — its differential half found a second bug, `{% if true %}` treating a literal as a name), and **PR #60** (two flakes: a test that failed for twenty minutes of every day, and a race asserting a field that arrives with a later message). **Two findings worth more than the code.** (1) The live probe cost 5 gateway calls against a budget of 4 — declared, not hidden — and the overage *was* the verification: `/audio/speech` returned **HTTP 500** because the request omitted `voice`, which the gateway requires and ignores. Left alone that ships a **permanently silent voice behind a fully green hermetic suite**; no stub can catch a field the stub does not require. (2) **Cloudflare Pages failed the build** while every local test passed, on a JSON import attribute node accepts and the Pages bundler rejects — assumption 26 now **settled false**, a local guard added and mutation-tested, and `safety.json` deleted rather than kept as a second source of truth. **Playbook rule 19**: an external check failing while ours pass is itself the finding.
- **2026-09-03 01:05 PDT — BUILD** — Launched **the ears** (`POST /api/transcribe` + the mic pointed at the same origin + a client-side recording cap), the last missing link in the chain the owner asked for: brain, voice and ears all live on the hosted page. Its brief carries the two lessons the previous slices paid for — *a stub cannot catch a field the stub does not require* (the silent-voice 500) and *a bundler-specific feature cannot be validated by the runtime the tests run on* (the Pages JSON-import failure) — and it must settle ledger assumptions 15 and 16 with a real call, since **nothing in the repo pins what a browser actually records**; the byte cap is not a duration cap for a compressed container, which is why the 15 s client cap is the honest ceiling. Also fixed **the second wall-clock flake** (PR #63): the telehealth bedtime window was `["00:00", "23:59"]`, which reads as "all day" but is false for exactly the minute 23:59, because the helper compares `start <= cur < end` — one guaranteed red a day. Found by the container-renderer agent while proving its *own* change innocent. Centred on `now` (wrapping handled), with a clock-free pair added alongside so the fix strengthens coverage instead of removing it. Both known clock-dependent tests are now gone.
- **2026-09-03 01:20 PDT — INTEGRATION** — Launched a pass on the merged state with two jobs worth more than a generic sweep. **(1) Prove the container change where it actually matters — inside the real image.** PR #62 shipped `jinja2` so a module's documented `{% if %}` renders in production, but its agent could only run a *build-mode* compose smoke; the brief demands a conditional-bearing prompt rendering **inside the running container**, and says explicitly that if the images-mode smoke pulls a published image predating the change, it must say so rather than claim coverage the run does not give. **(2) Sweep the whole class of clock-dependent tests instead of waiting for the next one to bite.** Two were found the hard way tonight — a scenario that fails for twenty minutes a day (PR #60) and a bedtime window false for exactly the minute 23:59 (PR #63) — so the agent must find every test that reads the wall clock, rule on each, and add a **ratchet guard** so a new one cannot appear unreviewed. That is the same move that worked for the untested-suite gap: convert a recurring class into a guard rather than fixing instances. Ears remain in flight; their files are reserved.
- **2026-09-03 01:10 PDT — AUDIT** — Clean on every mechanical check: **0** key hits in tracked files and in history, `mqtt/.env` and `.dev.vars` both ignored, **no Cloudflare account id anywhere in the tree** (it appears only in a transient dashboard URL), bundle 0-diff, all guards green, the three workflow templates byte-identical to their installed copies, no leftover branches, 7/7 releases pre-release. **Conformance, executed rather than read:** `call_with_backoff` genuinely honours a server `Retry-After` (told 7 s, slept 7.0 twice, then succeeded) and re-raises after exhausting `max_retries` — the control the public demo's limiter leans on. **One real finding, filed not rushed:** `mqtt/config.py`:81 defaults `MOXIE_LLM_BASE_URL` to *our* gateway, so a stranger cloning this public repo gets a supervisor silently pointed at the maintainer's server. Not a secret leak — no key is committed and the endpoint refuses unauthenticated calls — but it contradicts the stated "any sim, any gateway" principle, and the hosted Functions already do the right thing (`env.js` has no default; unset means degraded). It touches eight test files, so it is a slice, queued for the next BUILD slot rather than squeezed in as a third concurrent agent. **RESEARCH deferred this fire, honestly:** its purpose is keeping BUILD stocked, and the queue already holds sandboxed-extensions P0, broker-auth P1, the behavior planner and the live-Sim P1 remainder. Promotion deferred: the deep gate restarted when I pushed this audit's own finding.
- **2026-09-03 01:40 PDT** — **The hosted Sim now has ears**, and settling one unverified assumption was worth more than the feature. **Assumption 15 is FALSE:** the gateway's transcription endpoint decodes **PCM only** — one utterance in four containers gave WAV 200 word-perfect, and webm/Opus, ogg/Opus and mp4/AAC all **500**. That is exactly what a browser's `MediaRecorder` produces, and our Python path never hit it because `stt.py` always sends WAV. **The spec's "blast radius is contained" was wrong, which is the real lesson:** a 500 maps to `upstream_down` → 503, and the page degrades *wholesale* on a 503 — so forwarding a browser recording would have killed the brain and the voice on every mic press. A feature nobody could use would have broken two that worked. Fixed at the root: a container allowlist that refuses before spending a call, and `mic.js` now encodes 16 kHz mono WAV in the browser (`AudioContext` → decimate → s16 → RIFF) instead of shipping whatever the recorder made. Assumption 16 is moot rather than answered. Also merged **PR #65**: the container renderer proven *inside the running image* via `docker exec` through `ContentApp.respond()` (`STRIPPED == 0` proves the real renderer ran, `BLOCKED == 1` proves the sandbox still governs it), and the clock-flake class fenced by a ratchet whose three failure modes were each **negative-controlled by planting them** — a guard nobody has watched fail is not a guard. That sweep found a fourth flake nobody had hit: packets stamped 30 s before a captured "today" file under yesterday's roll-up for the half-minute after midnight. **Open honest gap:** real mic → browser encoder → gateway has never run end to end, because no test may open a microphone; each half is verified against the other, and one person with one browser settles it.
- **2026-09-03 05:40 PDT** — **The hosted Sim now has a brain, a voice and ears** (PR #66 merged): `functions/api/` carries `chat.js`, `speech.js`, `transcribe.js` and `health.js`, which is the whole chain the owner asked for, server-side. Merging it needed one fix-forward: the ears suite stubbed the microphone with `globalThis.navigator = {…}`, which **Node 21 made getter-only**, so it passed on this machine's Node 20 and threw on CI's Node 24 — the second instance of rule 19's shape in one night, after the Cloudflare bundler rejecting a JSON import attribute. Fixed with `Object.defineProperty` and, more usefully, `sim/tests/test_node_global_stubs.py` now forbids assigning to `navigator` or `crypto` in any node suite: comments stripped first so the prose explaining the rule cannot trip it (rule 17), a negative control on the exact line that broke CI, and an assertion that it is actually scanning files. Verified by re-introducing the assignment and watching it go red. Rule 19 generalised: *a feature cannot be validated by the runtime the tests happen to run on* — bundler, Node version or browser engine — and the durable fix is always to convert the remote-only failure into a local guard.
- **2026-09-03 05:55 PDT** — Ran the suite in the **main checkout** rather than a worktree and got **12 failures** that CI has never seen. Not a red: `mqtt/.env` exists only in the main checkout, and `config.py` loads it with `setdefault` at import, so every test that simulates "nothing configured" — monkeypatch-delete, reload — has the variable **refilled from the file**. Moving it aside: **3975 passed**. So those twelve tests assert nothing on any developer's machine and pass everywhere the file is absent, which is precisely where they run. **Playbook rule 20** records the general shape: an agent that only ever works in a worktree structurally cannot see this. Delegated as one slice with the audit's earlier finding, because both are `config.py` lying about what is configured: the hard-coded gateway default and the dotenv that defeats "unset". The brief's acceptance test is deliberately the main checkout **with** a real `.env` present, since that is the only place the second defect is visible.
- **2026-09-03 06:10 PDT — BUILD** — Launched **the offline fallback** (live-Sim P1), chosen over the bigger backlog items because of *when* it is seen: with no variables set — a fresh deployment, and **every preview deployment always** — the fallback is what every visitor gets, so it is the demo's first impression, and the owner named it explicitly. Measured the gap before briefing rather than trusting the spec: `stub.js` yields 11 reply lines and only **2** have clips, so 9 degraded replies fall through to a browser voice or silence, and none of `filler.py`'s thinking lines are cached at all. Local Piper voices are present (`en_US-amy-medium.onnx`), so the slice spends **zero** gateway calls. Also in scope: one in-character line spoken *once* on entering degraded, and skipping the 1.4 s Piper probe in that mode — 1.4 s of silence at exactly the moment the page is trying to prove it is alive. The brief's sharpest instruction is to **listen to what it ships**: decode a rendered clip and assert it is real speech, because a silent MP3 passes every structural check and fails the only thing that matters.
- **2026-09-03 06:25 PDT — INTEGRATION** — **Promoted `dev` → `main`, no tag** (main `ace2b18`, standing PR recreated as **#67**, dev reconciled with a verified-empty content diff, still 7 tags all pre-release). All seven deep-gate jobs green on the exact head. **`main` now carries the whole hosted chain** — `functions/api/`'s `health.js`, `chat.js`, `speech.js` and `transcribe.js` — so brain, voice and ears are on the release branch, not just on dev. **No integration agent this fire, deliberately:** two BUILD slices are mid-flight (the config-honesty fix and the offline fallback) and both change the tree an integration pass would measure, so it would validate a snapshot about to be wrong twice. The promotion *is* the end-to-end exercise — the gate runs the package build, the compose stack end to end, HIL and three multi-arch image builds — so this fire produced evidence rather than activity. Next integration pass runs against the merged result of both slices.
- **2026-09-03 07:05 PDT** — Merged **PR #68** (config honesty) and **PR #69** (the offline fallback). Both found a defect nobody had filed. #68: the acceptance test — the **main checkout with a real `mqtt/.env`** — went from **12 failed** to **4021 passed**, and its own class guard exposed a third defect I then fixed here: the documented `cp mqtt/.env.example mqtt/.env` set `MOXIE_VOICE_BASE_URL` to the string `"# e.g. …"`, truthy garbage the voice builder would treat as a gateway URL, and `MOXIE_APP` to its own comment — **the documented setup produced a broken appliance, silently**. #69: 18 clips at 452 596 bytes and **zero gateway calls**, every one decoded and passed through the shared speech predicate rather than merely counted (a silent MP3 passes every structural check and fails the only thing that matters), plus a latent tool bug — `prerender_audio.py` merged the manifest by group *name*, so any run without `--ambient` rewrote `index.json` without that key and silently orphaned 56 committed clips. Both slices verified their new tests **red against the old code** before trusting them. Pattern of the night, worth naming: **every genuinely valuable finding came from checking a claim in the one place nobody had looked** — inside the running container, in the main checkout, on the CI runtime, in the decoded audio.
- **2026-09-03 07:25 PDT** — Two agents launched, chosen for what the owner needs next rather than what the ranked backlog says. **(1) The Cloudflare deploy guide**, because it is the document a *human* reads to do the one part no agent can: it mentions **zero** `DEMO_` variables and **zero** `functions/`, both of which now exist on `main`, and its feature table **claims the child's voice works** — *"pre-rendered too — both sides"* — which the fallback slice reports is still mute. A guide promising a working feature is worse than one admitting a gap, so every row must be re-derived against the code and cited, with the deploy-only unknowns listed honestly and a curl the owner can run to learn which mode they landed in. **(2) The deferred integration pass** on the merged config + fallback state, briefed against the four specific risks those two carry rather than a generic sweep: the new loud failure could break the compose path whose `.env` handling just changed in two files; the dotenv change alters what a *running* supervisor reads at import; comment-stripping could truncate a real value containing `#`; and 18 clips are now load-bearing, so the manifest and the files must agree **in both directions** — the orphaning bug `prerender_audio.py` just had is worth a test that would have caught it, not one that confirms today.
- **2026-09-03 07:35 PDT** — Both freshly launched agents were killed within moments by a transient **HTTP 529 Overloaded** — server-side, no warning, no reset time to pace against — before either had created its worktree, so nothing was lost. Resumed both by message rather than relaunching (a relaunch duplicates work; a resume keeps the transcript). **Rule 15 generalised**: the per-step commit and resume-don't-relaunch discipline applies to *any* mid-flight termination, not just a usage limit. Added to both resumed briefs: an instruction that when a step's tooling is unavailable — no docker for the compose smoke, no reachable gateway for the live turn — the honest answer is "could not verify", never a substituted check described as coverage, because the compose path is precisely the one the config change was most likely to break. Also handed the docs agent one more stale claim to settle: the deploy guide states the bundle is "1.9 MB" in one place and "8 MB, ~100 files" in another, three lines from a per-file-limit claim a reader might rely on — measure one figure or drop it.
- **2026-09-03 08:00 PDT** — The deploy-guide agent was killed **twice** by HTTP 529 while my own main-loop calls kept working, so I stopped retrying the spawn and wrote it myself (**PR #70**) — the right call when the delegate path is failing and the facts needed verifying against code regardless. It corrected **two false claims**: the guide promised the **child's voice works, "both sides of the conversation"**, when `audio.js`:160 `speak(text, who)` accepts a `who` and no caller ever passes `"child"` (`bridge.js`:300,306 and `ambient.js`:106 pass nothing or `"ambient"`) — two committed clips, unreachable; and it blamed the mic on "no STT model" when a transcription route now exists. Its size figures also contradicted each other (**1.9 MB** vs **8 MB, ~100 files**) three lines from a per-file-limit claim a reader might rely on — measured **16 MB / 256 files / largest 3.2 MB**. The rewrite frames **"no configuration" as the safe default rather than a failure**, since that is what every preview deployment is in permanently, cites `env.js`:84-86 for the three required values, states the counters are best-effort because a Worker isolate is not a shared counter, and ends with the deploy-only unknown that fails safe plus one list of what does not work. **Lesson worth keeping: a doc claiming a working feature is worse than one admitting a gap, and the only way to know which you have is to read the callers, not the prose.**
- **2026-09-03 08:20 PDT** — The integration pass was killed by **HTTP 529 a third and fourth time**, so after resuming it once by message I ran the whole brief myself rather than keep feeding an unavailable tier. **All four risks came back clean**, and the two most valuable results came from places nobody had looked. (1) The **acceptance property** the config slice existed to create is real: the hermetic suite gives an *identical* **4020 passed / 16 skipped with and without a real `mqtt/.env` present** — the exact shape in which twelve tests once silently asserted nothing. (2) The **compose path survived** the `.env` change in *both* modes, build and images, each a full round-trip with real TTS audio; the loud failure is correctly scoped (only `llm`/`content` call `require_llm_base_url`; the `echo` app the smokes use needs no brain; importing `config` alone never exits). Comment-stripping truncates nothing (24/24 keys, 13/13 adversarial — `a#b`, `http://h/p#frag`, `sk-abc#def` intact, a *spaced* ` #` a comment), and the clips are an exact **88↔88 bijection, 0 orphaned**, the direction the `prerender_audio.py` bug actually failed in. A live turn answered through the real gateway brain **and** voice (`piper-amy`, 141544 B @ 22050 Hz, flatness 7.35e-02 — speech, not a tone). All four load-bearing citations in the hand-written deploy guide hold, including `/api/health` making no gateway call: `health.js` has no `fetch`, and `env.js`'s only `fetch(` is **prose inside a docstring** — the same comment false-positive class that reddened PR #52, which is why the `functions/` json-import guard strips comments before scanning. That guard I **mutation-tested with 9 mutants** (both attribute forms, bare/dynamic/`require` json imports, a stray `.json` file, a file nothing imports): all caught, baseline restored, checkout untouched. **Criterion 6 → 🟢 (5/6, ≈93%).** Two *environment* defects surfaced and were fixed rather than worked around: `sim/tests/.venv` lacked `paho-mqtt`, `jinja2`, `PyYAML` and `numpy`, which made a parity test fail and the live-turn test **skip for a reason unrelated to credentials** — a skip that reads as coverage is worse than a failure. **Lesson: a green suite proves nothing about the environment it did not run in — and my own first two measurements were void because I pointed at a `.venv` that does not exist, with the failure hidden by a `| tail` that returned 0.**
- **2026-09-03 09:05 PDT** — **The "deploy-only" unknowns were never deploy-only.** For days this plan recorded the spec's §10 assumptions 8, 9 and the `_headers` question as things only the owner could settle. They were settled from here in about two minutes, because **every branch push already publishes a public Cloudflare Pages preview** — the PR bot comment carries the URL. **Assumption 8 (the document's highest risk) is TRUE**: Pages routes `functions/` from the repo root even though `pages_build_output_dir = sim/web` — `GET /api/health` returned 200 JSON `gateway_not_configured`, so the feared silent 404-serving static site is not what we have. **Assumption 9 is TRUE**: `/api/_lib/*.js` serves the static HTML fallback, not module source — and note it answers **200 with HTML, not 404**, so anything probing for a missing route must check the content type, not the status. **The third came back the other way and it mattered (new assumption 27, FALSE): `sim/web/_headers` does not apply to a Pages Function response at all.** The control is clean — the same preview served `/sim.html` with the `/*` block's `Referrer-Policy` and served `/api/health` with none, while the two headers the Function *did* carry were exactly the two `envelope.js` sets in code. So §4.7's security block **never protected the two routes that can spend money**; the "belt and braces" comment described the only belt. Fixed in **PR #72**: `Referrer-Policy` is set in `envelope.js`, a mutation-tested guard fails if any header named in the `/api/*` block is missing from the code, and the fix was **verified on the preview** — `referrer-policy: same-origin` now ships where there was none. Assumption 11 is left open **on purpose**: the preview is keyless, but so is Production, so that proves nothing until Production-only variables exist. **Rule 21: when a document says "only a real deploy can settle this", open a PR and curl its preview before escalating to the owner.**
- **2026-09-03 09:55 PDT** — Two build agents returned, and **both reported that the slice I briefed was already built** — voice-picker P0 in PR #48, content-packs P0+P1 in PR #51. Neither rebuilt it; each verified the shipped code and went after what the brief's *emphases* actually required, and both found a real defect there. That is the better outcome, but the miss is mine: I ranked from a "Most valuable next slice" paragraph I already knew was stale, and briefed before fixing it. **Corrected here**, and the lesson is cheap — re-read the backlog's own status banners before writing a brief, because a spec that says *BUILT* at the top is faster to check than an agent is to run. **(1) The voice picker violated a standing owner rule.** As shipped, a console pick sat above every env value except `off`, so `MOXIE_TTS=piper` — *"we want to keep local TTS/STT as an option"*, written into a deployment on purpose — was silently overruled by a pick of `gateway:piper-ryan`, and that direction was never tested. An explicit `MOXIE_TTS`/`MOXIE_STT` now **pins the engine** (a pick *within* it still applies), enforced at three points so they cannot disagree. It also caught that `MOXIE_TTS=tone` is a **permission**, not a selection — and it is what both compose files default to, so treating it as a pin would have cut every `docker compose up` deployment's Speech dropdown to one entry (PR #77). **(2) A second live secret-disclosure hole, closed (PR #78).** `_minimal_render`'s whole grammar is a *bare dotted path*, walked with `getattr` over live context objects, and a content pack chooses every segment — so `{{ session.__class__.__repr__.__globals__.inspect.os.environ }}` rendered **5089 characters of process environment, `MOXIE_LLM_API_KEY` included, into the system prompt handed to the brain.** I reproduced it independently with a canary in place of the real key, and confirmed the fix blocks it while `{{ volley.config.child_pii.nickname }}` still renders. Honest reach: the shipped container was never exposed (it ships jinja2, and `SandboxedEnvironment` already refused it); the exposed shape is a bare `pip install moxie-cloud-sdk` without the `content` extra, which `pyproject.toml` deliberately supports. The fix gives the fallback **parity with the sandbox**, not a new rule. **This is the same shape as the template-injection hole closed on 2026-09-02: the fallback path nobody exercises is where the guarantee quietly isn't.** **(3) Two live suites ran nowhere (PR #79).** `test_live_gateway_stt.py` and `test_live_telehealth_voice.py` were dispatched by **no** tier since the day they were written, while a status-log line here claimed live STT coverage — and nothing swept them in, because the deep tier names *files*, so a `-k test_live_gateway` substring never applied. STT went live the same day as TTS and got the same claim but none of the enforcement: TTS has a guard that fails the job when its URL is empty, STT had none. Both are now dispatched (no new secret — they fall back to variables the step already requires), and a mutation-tested guard fails if any `test_live_*.py` on disk is dispatched by nothing. **Also: a transient Cloudflare Pages failure** on #77 that a rebuild cleared with no code change — worth knowing before anyone debugs one, since the branch's upload shape was byte-identical to `dev`'s (272 files, 16 MB). **And my own error, now rule 22:** I chained worktree cleanup behind `gh pr merge` in one command; the merge failed on a conflict, the cleanup ran regardless, and deleting the branch auto-closed the PR. Fully recovered via `refs/pull/78/head`, but it is rule 12 broken by its own author — the fix is mechanical, not attentional: gate cleanup on a *read-back* `MERGED`, never chain it.
- **2026-09-03 11:30 PDT** — **The audit refresh (PR #81) was the highest-value hour of the day, and it was spent on documentation.** Two agents had just lost a full run each to a stale Status column, so the fix went after the cause rather than the symptom: §3 of the feature audit is a **frozen 2026-09-02 snapshot** and nothing on the page said so, which is why its *"Us today"* column read like current state. It now carries a frozen/live table naming the two lost runs as the reason, a new §3.0 listing all **15 superseded cells** against the truth on `origin/dev`, and one editing rule — **🟢 only if you can name the file *and* the test; never brief an agent from §3.** Twenty-five rows were corrected and **every single one moved in the "we already shipped this" direction**, including `wakeup`, which the page still called its own *"most misleading gap"* long after PR #55 closed it. The re-ranked §4.4 now gives every row a **readiness verdict** (🟢 build-ready / 🟠 needs-a-spec / ⛔ blocked), because *"ranked #2"* and *"an agent can start this morning"* are different claims and conflating them is exactly what wasted the runs. **Two claims I verified myself rather than accepting:** the GHCR row — checked **anonymously**, which is what a stranger's `docker pull` actually does — all three images are **public** with `0.6.0/0.6/0.7.0/0.7/latest`, and `supervisor:0.7.0` is a real OCI index carrying **linux/amd64 + linux/arm64**; that retires a standing owner-ask, since the packages need no flipping. And the child-voice slice's claim that the demo cut her off: **ffprobe says her clip is 2.586 s and the reply fired at 1800 ms**, so the shipped demo has been truncating her mid-word at *"…it's my birthd—"* the whole time (PR #82 moves it to 3000 ms). **Upstream: verified nothing new**, twice over — all three heads, branches and tags unmoved, plus a sweep of the wider **40-fork network** since *"the two active forks"* decays. The tracker was not nothing: [openmoxie#60](https://github.com/jbeghtol/openmoxie/issues/60) reports a 24.10.801 robot crash-looping `bo-wifi.apk` when the `license` query is dropped — which **validates** our design (we publish `license_values: []` deliberately rather than dropping the answer) while leaving a precise residual risk, filed as blocked-on-a-robot and deliberately **not** "fixed" by inventing a fake credential. **The child's voice (PR #82)** shipped as a separate entry point rather than a flag — `speakClipOnly` plays only a clip this site authored for that exact sentence, with no route to Piper, `speechSynthesis` or the tone generator, so the guarantee is *which function you called* and cannot be loosened by editing a condition; the strict same-group lookup matters because `playClip`'s cross-bucket fallback would have let a child line play in Moxie's voice. The agent declined my suggested `replaying` gate with a better reason than I had: it would **mute `mic.js`'s degraded scripted line**, which runs outside a replay and is exactly where the child should be heard. **A tooling bug of my own, caught and fixed:** my merge watcher read a *previous* run's green after a push restarted CI, and tried to merge against it — `gh` refused (`UNSTABLE`), so nothing landed unverified, but it is the same class as the June gate bug that let #43–#48 through, on my side of the fence. The watcher now pins the head SHA and re-checks it has not moved before merging. **Owner-blocked, and now only two things:** the three Cloudflare Production variables, and — whenever convenient — whether **KV or a Durable Object** is available on this plan, which is the one unanswered question gating the top-ranked remaining slice (exact rate counters; today's are honestly labelled best-effort because a Worker isolate is not a shared counter).
- **2026-09-03 12:10 PDT** — **BUILD: waiting** — two agents in flight (sandboxed extensions P0, and an integration pass over the five slices that landed today), and the cap is two. But the *next* slice was checked before briefing rather than after, and the check paid for itself. **ADOPT #9 "printable launch-card QRs" is understated in the audit, and briefing it as written would have produced a feature that cannot work.** The row says *"the action-tag parser already understands `<launch:MOD[:CID]>`; what is missing is a sheet a parent prints"* — an **S**. Two facts contradict that. **(1) The setup app's QR grammar is provably closed.** `qr-commands.md`:95-100: `QRData.ParseFromString` has exactly three branches (`PA` pairing, `VN` VPN, else JSON), `debug.command` matches **exactly four** literals, and every other value hits a literal `else → State.QRDiagnostic`. There is no fifth handler in `bo-wifi`, and `Assembly-CSharp` has **zero** references to `QRCommand`. So a `GO<launch:MOD>` card is **not** actionable through the scanner that reads the pairing code. **(2) There is a second path, and it is the real one:** the on-robot *vision* QR reader raises **`eb-qr-event`**, whose scanned string rides `input_vars['$eb_qr_value']` and reaches our cloud as the `speech` of a `RemoteChatRequest` (`vision.md`:73-74, `presence.py`:18,75). We already **extract** that value — `presence.value_of` and `volley.input_var` both read it, and `test_presence_runtime.py`:127 even drives a QR event carrying the literal `GO<launch:DM>` — but **nothing acts on it**: there is no route from a QR value to a launch. The action-tag parser reads tags out of the *model's reply text* (cloud→robot); a QR value arrives in the opposite direction and never reaches it. So the slice is **two** pieces, not one — route a matching `$eb_qr_value` into a real launch action, *then* print the sheet — and it inherits BEYOND #9's honest ceiling: **no physical robot has ever sent us a vision event**, so the end-to-end claim would be unprovable on hardware whatever we build. Re-scoping it in the audit is owed before anyone is assigned to it; the file is reserved by the extensions agent right now, so this note is the record until it is free. **The pattern, for the third time today:** every wasted or near-wasted agent run this session came from trusting a backlog row instead of the code it describes. The audit refresh (#81) fixed the *staleness*; this one is different — the row was never right.
- **2026-09-03 20:55 PDT — BUILD + INTEGRATION** — **The demo went live on `moxie.mattvalancy.com`, and a 39-agent adversarial audit of the live surface came back with 23 confirmed findings, zero critical, zero high.** The verify pass earned its keep: it *demoted* the top finding from high to medium and corrected the auditor's own reasoning (the raw-PCM branch is **specified** at §3.2, so the defect is narrower and sharper than "a fallback exists" — `pcmFromAudio` is simply never told which format was asked for). Two slices shipped from it. **(1) `/api/health` was still answering from its P0-a stubs (PR #104).** `budgetState()` returned `null` and `loadState()` returned a hard-coded `{inflight: 0}`; their own comments read *"the wiring point is one line when limits.js lands"* — and limits.js had landed two commits earlier. So the probe **could not answer `budget_exhausted` at all**: a new visitor's page painted **LIVE on an over-budget deployment**, §7's BUSY pill could never fire, and every spend refusal was re-armed to `live` by the next 30-second poll. The live site's `inflight: 0` was a constant, not a measurement. Now wired to the real counters with §4.5's `Retry-After`, and the zero-upstream-call invariant is pinned **structurally** — `onRequestGet` is deliberately not `async`, because a handler that cannot await cannot await a gateway. **(2) The same PR corrected a document that promised a tier nobody built:** §4.6, ledger row 25, a health.js comment and one clause in the feature audit all described the counters as "an in-isolate map **plus the per-colo Cache API**". The Cache API leg **does not exist in the shipped code**. The multiplier is *isolates*, not colos — a materially weaker guarantee and exactly the number anyone sizing the spend risk would get wrong. The doc was corrected rather than the tier built, because assumption 13 (KV or a Durable Object on this plan?) is still open and decides which counter is worth building. **The preview finally proved environment separation** — it answers `gateway_not_configured` while Production answers `live`; that was untestable before, when neither had variables, and assumption 11 can now close. **A red PR of my own making, worth recording as the lesson of this fire:** the speech slice (#105) went red on `sim/test_wav_decode.mjs`, a caller of `pcmFromAudio` that asserts the old headerless behaviour — and **my brief's verify list never named that file**. The agent ran every test I gave it and all of them passed. CI runs **seven** `.mjs` suites; I listed three. A brief that specifies its own acceptance criteria incompletely will produce work that passes them and fails anyway, and the agent is not the thing that failed. **Rule 24: brief the CI test set from `sim/ci/ci.yml`, never from memory.**
- **2026-09-03 22:10 PDT — INTEGRATION** — **The hosted ears are proven, and the way they were proven is the point.** `/api/transcribe` was the one live route never exercised, and the DoD block had just been corrected to say so. Rather than probe it with a canned clip, the test closes a loop through all three routes: a real chat turn on the public domain produced *"The quick brown fox jumps over the lazy dog. What a fun sentence!"*, `/api/speech` redeemed that turn's ticket for **237 800 B of PCM @ 22 050 Hz** (5.39 s), the audio was resampled to the **16 kHz mono RIFF/WAVE** the gateway actually accepts — §10 assumption 15, settled live on 2026-09-03, is that it *rejects* webm/Opus, ogg/Opus and mp4/AAC, which is what a browser `MediaRecorder` emits — and posted back to `/api/transcribe`, which answered **200 in 2.93 s** with the sentence **word-perfect**, differing only in the final punctuation. Leak sweep over body and headers: no key, no gateway host, no STT model id, no Tailscale address. **Two things fell out of it that a narrower test would have missed.** (1) The response carried `load.inflight: 1` — the first *live* proof that PR #104's counter wiring works, where the stub it replaced could only ever answer `0`; the fix was verified in CI hours earlier but never observed doing its job in production. (2) The honest residual is now sharper, not vaguer: this loop used **synthesized speech and a hand-built WAV**, so it proves the route and the gateway but says nothing about `MediaRecorder` in a real browser on a real microphone — which is precisely the join §10 assumption 15 warns is fragile, since the one format a browser produces natively is the one the gateway refuses. **No integration agent was launched this fire, and that was the right call rather than a gap:** two agents were already mid-flight (the durable-telemetry red, and the admission queue), their file sets cover the Python runtime and the whole `functions/` tree between them, and a third would have had almost no disjoint surface. The valuable integration work left was a **read-only live validation**, which needs no worktree and cannot conflict with anything.

---
📖 [Implementation plan](implementation-plan.md) · [Vision](vision.md) · [Releasing](../../RELEASING.md) · [Docs index](../README.md)
