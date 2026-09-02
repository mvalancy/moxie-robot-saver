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

---
📖 [Implementation plan](implementation-plan.md) · [Vision](vision.md) · [Releasing](../../RELEASING.md) · [Docs index](../README.md)
