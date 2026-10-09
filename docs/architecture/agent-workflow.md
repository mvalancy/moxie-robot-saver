# Agent and contributor workflow

How changes get made in this repo, whether by a person or by an AI agent session. It replaces the
old orchestration log and session-loop page; their full history is in git.

## Goals, in priority order

1. The public Sim is an alive, playful, safe and affordable first meeting with Moxie.
2. The self-hosted cloud is complete: brain, voice, ears, content, management, and interchangeable
   clients (Sim or robot).
3. Adopt the best of [OpenMoxie](https://github.com/jbeghtol/openmoxie) behind our contracts
   (see the [feature audit](openmoxie-feature-audit.md)).
4. Grow the brain-agnostic platform ([vision](vision.md)) without claiming physical-robot results we
   have not measured on a real robot.

Settled product decisions: Moxie is a **meet-Moxie toy** with effortless chat (not structured missions),
and the only analytics is the privacy-first aggregate Cloudflare measurement.

## Hard rules

- **Clean room.** Build from `docs/architecture/` and `docs/reverse-engineering/`. Never read the vendor
  Android app or its decompiled output. OpenMoxie is MIT and fine to study; credit it.
- **Secrets.** Never print or commit keys. `mqtt/.env` stays untracked; a staged diff must contain no
  `sk-…`. Deployment hostnames, keys and account ids are configuration, never hard-coded.
- **One branch, one PR.** Every change is a `feat/*` branch (in its own git worktree) with a PR into
  `dev`. Nobody commits directly to `dev` or `main`. Promotion to `main` follows
  [RELEASING.md](../../RELEASING.md); tag a release only on the owner's word.
- **Merge only green.** The gate is `scripts/pr-green.sh <pr>` (every check complete and passing, SIL
  included), re-read in the same command that merges — a result is only true for the commit it was read on.

## Agent brief protocol

Every agent brief carries these lines:

- **Isolation:** `git worktree add ../wt-<slice> -b feat/<slice> origin/dev`; work only there; do not
  touch `main`/`dev`.
- **Venv:** `python3 -m venv .venv && .venv/bin/pip install -q -r sim/tests/requirements.txt`. That file
  is the one declaration of what the suite needs — never hand-list packages. Add `piper-tts
  faster-whisper` only for local-voice work (about 2 GB).
- **Quality gates:** a test for every feature; the hermetic suite green with credentials blanked
  (`MOXIE_LLM_API_KEY= .venv/bin/python -m pytest sim/tests -q -k "not test_sil and not test_live"`);
  doc guards (`python3 sim/tools/build_docs_bundle.py`, `scripts/check-doc-links.py`,
  `scripts/check-doc-consistency.py`, `node sim/test_docs.mjs`); a SIL smoke on a free port when the
  runtime changed (`MOXIE_SIL_PORT=19xx bash sim/run_smoke.sh`).
- **Live calls** happen only in an explicit step with a stated cap (six attempts, retries included, by
  default).
- **Report:** branch, commits, what shipped, test counts, guard results, and honest gaps, in under 400
  words.

`sim/tests/test_ci_workflows.py` fails if the venv line above drifts back into a package list.

## Integration rules

Each rule below was learned from a real failure.

| Rule | Why |
|---|---|
| Merge `origin/dev` into the branch and re-run guards before opening the PR. | The branch base is older than whatever landed meanwhile. |
| Resolve conflicts in `sim/web/docs-*` by regenerating the bundle, never by hand. | They are build outputs. |
| Run concurrent agents only on provably disjoint files; list the other agents' files as reserved in each brief. | Two writers on one region lose work. |
| Confirm the PR says `MERGED` before deleting a worktree or branch, and never chain cleanup after a merge in one command. | Deleting an open PR's head branch closes the PR. |
| Ask the PR whether it merged, not `git merge-base --is-ancestor`. | Squash merges are never ancestors. |
| After `gh pr merge --delete-branch`, check the remote branch is really gone. | A worktree holding the local branch aborts the remote delete. |
| Read a guard's success line, not just `tail -1`. | Failures print above the last line. |
| Browser tests assert recorded state after completion, never a live sample. | Sampling in a short window is a flake on a loaded runner. |
| When an external check (e.g. the Pages preview build) fails while ours pass, that asymmetry is the finding. | The preview bundler caught what the hermetic suite could not. |
| Settle deploy questions by opening a PR and curling its Pages preview. | Every branch push publishes a public preview. |
| When a check says the product did X, measure X before changing the product. | Fixes built on an unmeasured claim do not hold. |
| A note in code or a spec is true only as of its commit; check what landed since before acting on it. | Later PRs silently invalidate earlier design notes. |
| Do not work inside an agent's worktree until the agent is confirmed idle. | A completion notice does not mean it stopped. |
| `pytest.importorskip` skips only on `ModuleNotFoundError`; simulate a missing package accordingly. | An `ImportError` stub fails instead of skipping. |

## Recurring session loops

A scheduler may queue duties into **one** session (never several competing writers):

| Cadence | Duty |
|---|---|
| 1 hour | Deliver one bounded, verified increment toward the highest ready goal. |
| 2 hours | Trace one real user path and find one evidenced gap. |
| 3 hours | Rotate one security, spending, performance, privacy, browser, lifecycle or test boundary. |
| 4 hours | Build a status packet for an independent, read-only strategy review. |
| Daily (a standing owner goal) | Grow the creature: add a few `sim/web/ambient.json` lines (or a gesture in `ambient.js`'s keyframe style) in her register: mischievous, odd, secretly devoted; mock-sinister plans that end harmless; never menace aimed at the child, nothing scary at bedtime. Extend the habits the chat persona names; seasonal lines carry `"months"`. Render in `tts-piper-kristin` with `prerender_audio.py --engine gateway` under a call ledger, check every clip word for word with a local whisper (reword on a miss), and keep `node sim/test_ambient.mjs` and `node sim/test_fallback_coverage.mjs` green. |

Rules for each batch:

- Refresh git, PRs, checks and deployed state first. Never carry a PR number or run id in a prompt.
- State the question, acceptance criterion, owned files and stopping condition before editing.
- End with one of `shipped`, `verified`, `hypothesis-falsified`, `blocked` or `clean-noop`. A commit is not
  progress, and no work is invented to fill an interval. No status-only commits.
- At most one heavy browser or suite workload at a time.
- Two unchanged failures require a changed hypothesis, instrument or environment. Three encounters with
  the same external blocker park it until its reopening condition changes.
- Missed fires coalesce into one batch after an outage; they never become a catch-up storm.
- The reviewer suggests prompt changes; it cannot edit cadences, goals, budgets, privacy rules or release
  authority.

See also the `running-layered-session-loops` skill in `.claude/skills/`.

## Known limits to keep visible

- `/api/health` does not call the gateway, so `mode=live` means *configured and within local limits*, not
  *the upstream just answered*.
- The edge rate-limit counters in `functions/api/_lib/limits.js` are per-colo and fail open; they are not a
  global spending ceiling.
- Physical-robot claims stay unproven until tested on a real Moxie.

---
[Architecture index](README.md) · [Roadmap](../../ROADMAP.md) · [Release process](../../RELEASING.md)
