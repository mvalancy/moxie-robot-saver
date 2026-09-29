# sim/ci — GitHub Actions workflow templates

These files are the source of truth for CI. Each one is mirrored, byte for byte, to
`.github/workflows/`. Edit the file here and copy it across **in the same commit**;
`sim/tests/test_ci_workflows.py` fails if the two differ.

```sh
cp sim/ci/ci.yml sim/ci/ci-deep.yml sim/ci/release.yml sim/ci/deployed.yml sim/ci/promotion.yml sim/ci/cleanup.yml .github/workflows/
```

Pushing under `.github/workflows/` needs a token with `workflow` scope.

| File | Trigger | What it checks |
|---|---|---|
| `ci.yml` (fast) | push to `dev`, PR into `dev` | Three parallel jobs: docs and protocol guards; SIL smoke plus the whole pytest suite; every browser suite, including the `--selftest` of the two deployment checks below. |
| `ci-deep.yml` (deep) | PR into `main`, nightly at 03:17 UTC, manual dispatch | Everything above, plus the package build, SIL scenarios, broker outage, the compose stack (built and prebuilt images), a soak test and multi-arch image builds (not pushed). Live tiers on dispatch only. |
| `release.yml` | tag `v*` | sdist and wheel (version must equal the tag), GitHub Release, multi-arch images to GHCR. See [`RELEASING.md`](../../RELEASING.md). |
| `deployed.yml` | four times a day, manual dispatch | The live deployment in a phone-sized browser (see below). |
| `promotion.yml` | hourly at :37, manual dispatch | That `dev` was reconciled after the last `dev → main` promotion (see below). |
| `cleanup.yml` | PR closed | Deletes that PR's cache namespace. |

## The live tiers in `ci-deep.yml`

Only on manual dispatch, never on a PR, because they spend real gateway calls:

```sh
gh workflow run ci-deep.yml --ref dev                  # live gateway tier (about 12–13 completions)
gh workflow run ci-deep.yml --ref dev -f voice=true    # plus real Piper speech ⇄ real faster-whisper
```

- **Live gateway tier:** `test_live_gateway.py`, `test_live_action_tags.py` and
  `test_live_content_e2e.py`, using the `MOXIE_LLM_API_KEY`, `MOXIE_LLM_BASE_URL` and
  `MOXIE_LLM_MODEL` repo secrets.
- **Live voice tier:** `test_live_talk_e2e.py`.

Live tests skip cleanly without credentials, which would make a dispatch look green while proving
nothing. So on a dispatch the gateway step fails if the key is empty, and the voice step fails
unless at least 3 of its 4 tests actually ran. Both write counts and skip reasons to the job
summary.

## `deployed.yml` — checks against the real deployment

Every other check tests a local server. This one runs [`sim/check_deployed.mjs`](../check_deployed.mjs)
against the deployed site, because Cloudflare injects an analytics script into production pages
that no local run can see. It checks that the chat box is reachable on a phone without opening
the control rail, and that the page raises no CSP violation.

```sh
node sim/check_deployed.mjs                    # the canonical origin declared in sim/web/index.html
node sim/check_deployed.mjs https://host/sim   # any deployment (or MOXIE_DEPLOYED_URL=…)
node sim/check_deployed.mjs --selftest         # hermetic; the fast tier runs this on every push
```

It is a monitor, not a merge gate: Pages previews create no GitHub Deployment event, a fresh
branch alias serves the previous build, fork PRs get no preview, and previews do not carry the
injected script anyway. The header of `deployed.yml` has the details.

The same file has a separate, dispatch-only microphone job,
[`sim/check_hosted_mic.mjs`](../check_hosted_mic.mjs), which plays a recorded voice into Chrome's
fake microphone:

```sh
gh workflow run deployed.yml -f mic=dry                    # free: button, permission, the encoded WAV
gh workflow run deployed.yml -f mic=spend -f mic_budget=5  # real calls (about 3)
node sim/check_hosted_mic.mjs --selftest                   # hermetic; the fast tier runs this
```

It is kept off the schedule because each paid run spends the public demo's budget, and
`test_live_hosted_ears.py` already covers the gateway side wherever a key exists. The
`--selftest` proves the capture path and that the uploaded audio is the clip that was played; it
does not measure recording fidelity, because CI runners' fake-microphone capture saturates. See
the [SIL and CI design](../../docs/architecture/sil-and-cicd.md) for the measurements.

## `promotion.yml` — reconcile after a promotion

Squash-merging `dev → main` leaves `dev` one commit behind `main`. Nothing turns red, so it was
often missed. [`sim/tools/check_promotion_state.py`](../tools/check_promotion_state.py) checks for
it hourly and fails if `dev` is still behind 30 minutes after the squash (reconciles have taken
11 s to 990 s). It runs on a schedule because the defect is a push that never happened, so no push
trigger would catch it. The fix is in [`RELEASING.md`](../../RELEASING.md).

```sh
python3 sim/tools/check_promotion_state.py        # 0 reconciled · 1 not reconciled · 2 could not measure
gh workflow run promotion.yml -f grace_seconds=0  # in CI, ignoring the grace window
```

`sim/tests/test_promotion_guard.py` tests the checker against real git repositories.

## `fetch_piper_voices.py`

Piper voice models (`sim/tts/voices/*.onnx`, 63 MB each) are git-ignored. This script downloads
them, pinned to the `v1.0.0` tag of `rhasspy/piper-voices`, checks size and sha256, skips files
that are already correct, and uses only the standard library.

```sh
python3 sim/ci/fetch_piper_voices.py            # into sim/tts/voices/
python3 sim/ci/fetch_piper_voices.py --check    # verify only; exit 1 if anything is missing
```

---
[Releases and CI tiers](../../RELEASING.md) · [SIL and CI design](../../docs/architecture/sil-and-cicd.md) · [The test suites](../tests/README.md)
