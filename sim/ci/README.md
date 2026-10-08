# `sim/ci/` — CI workflow templates

The source of truth for our GitHub Actions workflows, plus the voice fetcher the live voice tier uses.

## Files

| File | Tier | Trigger | What it proves |
|---|---|---|---|
| [`ci.yml`](ci.yml) | fast (dev) | push `dev`, PR → `dev` | doc/protocol guards, the hermetic pytest suite, SIL smoke + scenarios, Pages Functions tests, and the headless-Chrome browser suites |
| [`ci-deep.yml`](ci-deep.yml) | deep (main) + HIL | PR → `main`, nightly 03:17 UTC, manual dispatch | the above, plus the SDK package build, the compose stack, a multi-arch build, the soak, and (dispatch only) the live tiers |
| [`release.yml`](release.yml) | release | tag `v*` | sdist + wheel, version == tag, GitHub Release |
| [`deployed.yml`](deployed.yml) | monitor | 4×/day (`23 2,8,14,20 * * *`), canary 1×/day (`41 15 * * *`) + dispatch | the live deployment in a phone-sized browser: composer reachable with the rail shut, Cloudflare's injected beacon loads with zero CSP violations, the canonical origin reads live; a daily one-turn canary that the brain answers; a dispatch-only mic job |
| [`promotion.yml`](promotion.yml) | monitor | hourly at :37 + dispatch | the last `dev → main` promotion was finished (`dev` is not behind `main`) |
| [`cleanup.yml`](cleanup.yml) | cleanup | PR closed | deletes that PR's cache namespace only |
| [`fetch_piper_voices.py`](fetch_piper_voices.py) | helper | — | fetches the two Piper voices (Amy, Lessac) pinned to `rhasspy/piper-voices` `v1.0.0`, sha256 + size verified, idempotent, stdlib only |

## Install and run

```sh
# templates → installed copies, in the SAME commit (sim/tests/test_ci_workflows.py asserts byte-identity)
cp sim/ci/{ci,ci-deep,release,deployed,promotion,cleanup}.yml .github/workflows/

gh workflow run ci-deep.yml --ref dev                  # live gateway tier (creds only)
gh workflow run ci-deep.yml --ref dev -f voice=true    # + live voice tier (real Piper ⇄ real Whisper)
gh workflow run ci-deep.yml -f soak_profile=week       # soak profile: quick (default) · smoke · week (nightly)
gh workflow run deployed.yml                                # free check + the canary (ONE chat turn)
gh workflow run deployed.yml -f canary=false                # free check only
gh workflow run deployed.yml -f url=https://<branch>.<project>.pages.dev/sim -f canary=false
gh workflow run deployed.yml -f mic=dry                     # free: button, getUserMedia, encoded WAV
gh workflow run deployed.yml -f mic=spend -f mic_budget=5   # real: ~3 gateway calls
gh workflow run promotion.yml -f grace_seconds=0            # check now, no post-squash grace

node sim/check_deployed.mjs [URL] | --selftest      # also MOXIE_DEPLOYED_URL; the fast tier runs --selftest
node sim/check_hosted_mic.mjs --selftest            # hermetic; the fast tier runs this
node sim/check_live_turn.mjs [URL] | --selftest     # ONE chat turn (spends); the fast tier runs --selftest
python3 sim/tools/check_promotion_state.py          # 0 finished · 1 unfinished · 2 could not measure
python3 sim/ci/fetch_piper_voices.py [--dest DIR] [--check] [--force]   # also MOXIE_VOICES_DIR
```

## Gotchas

- **Two copies, one commit.** Edit here, `cp` to `.github/workflows/`, commit both together. The gh
  OAuth token may lack `workflow` scope; pushing over SSH works. Verify after a push with
  `git diff --quiet origin/<branch>:sim/ci/<f>.yml origin/<branch>:.github/workflows/<f>.yml`.
- **The fast tier runs pytest twice on purpose**: an early `-k "not test_sil and not test_docs"` pass
  (fails fast, before the browser install) and one unfiltered pass.
  `test_ci_workflows.py::test_the_fast_tier_runs_the_whole_pytest_suite` requires the unfiltered
  run, so a future `importorskip` reddens instead of skipping. Dependencies come from
  `sim/tests/requirements-hermetic.txt` (+ playwright in `requirements.txt`), not from the YAML.
- **Live tiers are `workflow_dispatch` only** — they spend real gateway calls and fork PRs get no
  secrets. They use the `MOXIE_LLM_API_KEY` / `MOXIE_LLM_BASE_URL` / `MOXIE_LLM_MODEL` secrets. They
  **fail rather than skip**: the creds step fails on an empty key, and the voice step needs 3 of its
  4 tests to actually pass. Both write counts and skip reasons to the job summary.
- **`deployed.yml` is a monitor, not a merge gate.** The Pages integration creates no GitHub
  Deployment (so `deployment_status` never fires), branch aliases 404 before their first build, fork
  PRs get no preview, and no `*.pages.dev` host carries the analytics beacon. The full argument is in
  the file's header.
- **The canary spends one chat turn a day, and only that.** The free check cannot see a dead brain
  (`/api/health` reads configuration only and the checker aborts every spending route), so the
  `canary` job runs `check_live_turn.mjs` on its own cron: one `POST /api/chat`, a browser user agent
  and the site's own Origin, reason null, a reply, a voice ticket it never redeems, under 10 s. At
  most two POSTs, the second only after a free `rate_limited`. Each job keys on the cron that fired
  it (`github.event.schedule`); `sim/tests/test_ci_deployed_monitor.py` fails if a cron and an `if:`
  drift apart. On the canonical origin the free check also requires mode `live` and the badge
  MOXIE ONLINE (`expect_live` overrides; previews are not held to it).
- **The mic job is off the schedule** because each spend run costs gateway calls out of the shared
  demo budget; `test_live_hosted_ears.py` already covers the ears wherever a gateway is configured.
  The runner's mic capture saturates, so recording identity and fidelity are checked only by
  `--dry-run` and the paid run; the push gate uses a scorer proof over committed fixtures instead.
- **Why promotion is a schedule, not a fast-tier step**: the defect is a missing reconcile push, so
  there is no push to trigger on. Squash-merging `dev → main` leaves `dev` one commit behind; the
  check forgives 30 minutes after `main`'s tip (observed reconcile gap: 11–990 s). Its teeth live in
  `sim/tests/test_promotion_guard.py`. The corrected order is in [`RELEASING.md`](../../RELEASING.md).
- Piper `.onnx` voices (63 MB each) are git-ignored; CI caches them keyed on the pinned release.

---
📖 [sim](../README.md) · [SIL & CI/CD](../../docs/architecture/sil-and-cicd.md) · [Back to top](../../README.md)
