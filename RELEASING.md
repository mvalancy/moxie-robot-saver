# Branches, CI and releases

```
feat/*  ──PR──▶  dev  ──PR──▶  main  ──tag vX.Y.Z──▶  GitHub Release + GHCR images
         fast CI       deep CI              release workflow
```

## Branches

| Branch | Role | Gate |
|---|---|---|
| `feat/*` | One change. Short-lived, branched off `dev`. | PR into `dev` runs fast CI. |
| `dev` | The integrated, always-green release candidate. | PR into `main` runs deep CI. |
| `main` | Released code. | A `v*` tag runs the release workflow. |

- Every change, including automated work, goes `feat/*` → PR → `dev`. Nobody commits directly to `dev`
  or `main`.
- **Promotion** (`dev` → `main`) happens only for an owner-approved milestone. Open the PR, merge after
  deep CI passes. `bash scripts/standing-pr.sh` prints the open promotion PR, or `none`.

### After a promotion: reconcile `dev`

Promotions are squash-merged, so `main` gets one new commit that `dev` has never seen. Right after the
merge, on `dev`:

```bash
git fetch origin && git merge origin/main -X ours --no-edit
git diff <dev-before-merge> HEAD   # must print nothing; otherwise stop and investigate
```

[`sim/ci/promotion.yml`](sim/ci/promotion.yml) runs
[`sim/tools/check_promotion_state.py`](sim/tools/check_promotion_state.py) hourly and fails if `dev` is
still behind `main` more than 30 minutes after a promotion.

## CI tiers

| Tier | Workflow | Trigger | What runs |
|---|---|---|---|
| Fast | `ci.yml` | push to `dev`, PR into `dev` | Doc and protocol guards, SIL smoke, unit and browser tests |
| Deep | `ci-deep.yml` | PR into `main` | Full suite, the compose stack, hardware-in-the-loop against the virtual robot, image builds (not pushed) |
| Deep, live | `ci-deep.yml` by hand | `gh workflow run ci-deep.yml --ref dev` | Adds the live gateway suites (`test_live_gateway`, `test_live_action_tags`, `test_live_content_e2e`). Spends about 12–13 real completions; fails if the secret is missing. |
| Deep, live voice | same, with `-f voice=true` | manual | Adds `test_live_talk_e2e`: real Piper speech into real Whisper. |
| Release | `release.yml` | tag `v*` | Builds and publishes the package and images (below) |
| Deployed | `deployed.yml` | schedule | Checks the hosted site in a real phone-sized browser 4× a day and spends one chat turn a day on a canary; not a merge gate. |
| Cleanup | `cleanup.yml` | PR closed | Deletes that PR's build cache. |

CI keeps no durable artifacts (the repository's retention window is 7 days). The only durable outputs are a
tagged Release and its images.

### Workflow templates

The workflows are edited in [`sim/ci/`](sim/ci/) and copied to `.github/workflows/` in the **same
commit** (`cp sim/ci/<file> .github/workflows/<file>`); a check verifies they match. Pushing
`.github/workflows/` needs a token with the `workflow` scope. Live suites read the gateway key and
other endpoints from repository secrets, never from the tree.

## What a release publishes

A `v*` tag runs two independent jobs, so a registry failure cannot cost the Python release (and neither
is allowed to fail silently):

| Job | Output |
|---|---|
| `build-and-release` | `moxie_cloud_sdk-<version>` sdist and wheel on a GitHub Release. Fails if `__version__` ≠ the tag. |
| `publish-images` | `linux/amd64` + `linux/arm64` images on GHCR |

| Image | Built from |
|---|---|
| `ghcr.io/mvalancy/moxie-robot-saver/supervisor` | `mqtt/Dockerfile` |
| `ghcr.io/mvalancy/moxie-robot-saver/console` | `server/Dockerfile` |
| `ghcr.io/mvalancy/moxie-robot-saver/broker-certs` | `mqtt/broker/Dockerfile` (one-shot certificate minter) |

These are the names [`docker-compose.images.yml`](docker-compose.images.yml) uses. There is no broker
image: the broker is upstream `eclipse-mosquitto:2.0.20` with our config.

Tag `v0.6.2` produces image tags `0.6.2`, `0.6` and `latest`; a pre-release such as `v0.6.2-rc.1`
produces only `0.6.2-rc.1`. The job authenticates with the built-in `GITHUB_TOKEN`.

## Versions

- One source: `__version__` in `mqtt/moxie_sdk/__init__.py` (`pyproject.toml` reads it).
- Semver. Before 1.0, `Y` is features (breaking allowed) and `Z` is fixes. Everything before 1.0 is
  marked pre-release.
- Bump `__version__` on `dev` in its own small PR just before a promotion, so the promotion carries it
  to `main` and the reconcile diff (above) stays empty.

## Promotions are not releases

A promotion is the end-to-end exercise and can happen whenever the owner approves a milestone. A **tag**
publishes a Release and three image versions, so it is cut **only on the owner's explicit word**, never
for a routine promotion or a version bump.

## Cutting a release

1. Open the `dev` → `main` PR; deep CI must pass.
2. Check the PR already carries the `__version__` you will tag (bumped on `dev` first; see Versions),
   then merge.
3. With the owner's approval: `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. Check `docker pull ghcr.io/mvalancy/moxie-robot-saver/supervisor:X.Y.Z` works.
5. Reconcile `dev` (above).

To build the package locally: `cd mqtt && python -m build`.

---
[Repo structure](STRUCTURE.md) · [Roadmap](ROADMAP.md) · [Agent workflow](docs/architecture/agent-workflow.md)
