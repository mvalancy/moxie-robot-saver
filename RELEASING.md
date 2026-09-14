# 🚀 Branching, CI tiers & releases

A three-tier flow: fast feedback on `dev`, deep validation into `main`, versioned packages out.

```
feat/*  ──PR──▶  dev  ──PR──▶  main  ──tag v X.Y.Z──▶  release (package)
          fast CI      deep CI + HIL sim          release workflow
     (unit·docs·SIL)  (full suite · sim-in-the-loop ·
                       eventually real hardware/servers)
```

## Branches

| Branch | Role | Gate |
|---|---|---|
| **`feat/*`** | One feature/slice. Short-lived. | PR → `dev` runs **fast CI**. |
| **`dev`** | Rolling **release candidate** — integrated, always-green. Autonomous build loops land here. | PR → `main` runs **deep CI**. |
| **`main`** | Released, tag-able, deploy-quality. | Tag `vX.Y.Z` → release workflow. |

- **Features / contributors:** branch `feat/<name>` off `dev`, PR into `dev` (fast CI gates it).
- **Build loops:** commit to `dev` (the integration RC branch). Larger/riskier work still uses a
  `feat/*` → `dev` PR.
- **Promotion:** only for an owner-approved major project milestone, open a `dev → main` PR and merge
  it after deep CI passes. `bash scripts/standing-pr.sh` resolves that PR when it exists and prints
  `none` between milestones; never hardcode its number.
- **After a promotion (squash) — reconcile `dev`:** a squash-merge gives `main` one commit that shares
  **no ancestry** with `dev`'s granular history, so both branches look like they independently "added"
  the same files. Fix the ancestry immediately after the squash: on `dev`, run
  `git fetch origin && git merge origin/main -X ours --no-edit`. **Verify
     `git diff <pre> HEAD` is empty BEFORE pushing** — if it prints anything, the `-X ours`
     swallowed a real change and the promotion needs unpicking, not pushing.

  **This is checked mechanically.** Reconciliation was missed after five of seven promotions by
  three different actors, so
  [`sim/ci/promotion.yml`](sim/ci/promotion.yml) runs
  [`sim/tools/check_promotion_state.py`](sim/tools/check_promotion_state.py) hourly and **reddens**
  if `dev` is left behind `main`, forgiving the first 30 minutes after the squash (the measured
  window: ten promotions reconciled in 11s–990s). It deliberately does **not** require a standing PR.

  (Alternative: promote with a **merge commit** instead of squash — `main` keeps full history and no
  reconcile is needed. We use squash for a clean one-commit-per-release `main`, and pay the reconcile.)

## CI tiers

| Tier | Workflow | Trigger | Runs |
|---|---|---|---|
| **Fast** (dev) | `.github/workflows/ci.yml` | push `dev`, PR → `dev` | docs/protocol guards + SIL smoke + unit/cloud tests (~5 min) |
| **Deep** (main) | `.github/workflows/ci-deep.yml` | PR → `main` | full suite + **HIL sim** (hardware-in-the-loop: a virtual robot end-to-end, later a real robot on a self-hosted runner) |
| **Deep — live** | same, `workflow_dispatch` | `gh workflow run ci-deep.yml --ref dev` | the above **+ the live gateway suites** (`test_live_gateway` · `test_live_action_tags` · `test_live_content_e2e`) on the real brain via repo secrets. **Spends ≈12–13 real gateway completions** — hence manual, never on a PR. Fails (not skips) if the secret is empty. |
| **Deep — live voice** | same, dispatch input | `gh workflow run ci-deep.yml --ref dev -f voice=true` | the above **+ `test_live_talk_e2e`**: real Piper speech ⇄ real faster-whisper. Installs the voice deps and fetches 2 × 63 MB pinned Piper voices (cached); ~1 more completion. Fails if fewer than 3 of its 4 tests actually ran. |
| **Release** | `.github/workflows/release.yml` | tag `v*` | **two parallel jobs** — (1) sdist+wheel, verify `__version__`==tag, GitHub Release; (2) **multi-arch container images → GHCR** (below) |

## What a `v*` tag publishes

Two independent jobs, deliberately with no `needs:` between them — a flaky registry must not
cost you the Python release, and nothing is `continue-on-error`, so a failed image push is a
**red job on the tag**, never a silent skip.

| Job | Output | Permissions |
|---|---|---|
| `build-and-release` | `moxie_cloud_sdk-<version>.{tar.gz,whl}` attached to a GitHub Release | `contents: write` |
| `publish-images` (matrix ×3) | `linux/amd64` + `linux/arm64` images pushed to GHCR | `contents: read`, `packages: write` |

The images — these are exactly the names [`docker-compose.images.yml`](docker-compose.images.yml)
references, so an owner installs with **no clone at all** (see
[the guide](docs/guides/one-command-stack.md)):

| Image | Built from | Contents |
|---|---|---|
| `ghcr.io/mvalancy/moxie-robot-saver/supervisor` | `mqtt/Dockerfile` (context `./mqtt`) | the robot-cloud supervisor, no ML wheels |
| `ghcr.io/mvalancy/moxie-robot-saver/console` | `server/Dockerfile` (context `.`) | the parent console + web client |
| `ghcr.io/mvalancy/moxie-robot-saver/broker-certs` | `mqtt/broker/Dockerfile` | the one-shot per-appliance CA/cert minter |

There is no `broker` image on purpose: the broker **is** upstream `eclipse-mosquitto:2.0.20`, and
we ship its config (inlined in `docker-compose.images.yml`), not a fork of it.

Tags applied, from the git tag:

| Git tag | Image tags |
|---|---|
| `v0.6.2` | `0.6.2`, `0.6`, `latest` |
| `v0.6.2-rc.1` | `0.6.2-rc.1` only — a pre-release must never move `latest` or the `0.6` channel |

Every image carries OCI labels `source`, `revision`, `version`, `licenses=MIT`, `title`,
`description`, `url`, `vendor`, so a pulled layer says which commit built it. Auth is the built-in
`${{ secrets.GITHUB_TOKEN }}` with job-scoped `packages: write` — **no new secret**. The first push
creates the packages as *private*; make them public once, on the repo's Packages page, or an owner's
`docker pull` gets `denied`.

## Version numbering (semver)

Single source: `mqtt/moxie_sdk/__init__.py` `__version__`; `pyproject.toml` reads it.

- **`X.Y.Z`** — MAJOR.MINOR.PATCH. Pre-1.0 (`0.Y.Z`): `Y` = features (breaking allowed), `Z` = fixes.
- **Release candidates** on `dev`: `X.Y.Z-rc.N` (tag `vX.Y.Z-rc.N` for a pre-release).
- **Release** on `main`: `X.Y.Z` (tag `vX.Y.Z`). The release workflow **fails if `__version__` ≠ the tag**.
- Bump `__version__` in the `dev → main` promotion PR: `Z` for fixes, `Y` for a milestone (e.g. M-set complete).

## Release cadence — promotions are not releases

**Promotion (dev → main) is the end-to-end exercise; a tag is a major project milestone.** The deep gate on a
promotion PR builds the package, runs the compose stack and HIL end to end, and builds all
three images multi-arch *without pushing* — so `main` moves whenever `dev` is a green RC. A **tag**
(which publishes a GitHub Release and three GHCR image versions) is cut **only when the owner explicitly
approves a major milestone** — never for a routine promotion, intermediate test pass, or arbitrary version
increment.
Everything before 1.0 is marked **pre-release**. (Owner rule, 2026-09-02: "don't clog GitHub with
unlimited packages/releases since they aren't good yet, but exercise the whole system end to end.")

CI creates **no durable Actions artifacts**. Package builds, contact sheets, soak results, and Buildx
diagnostic records are validation intermediates; their check/log is enough. The only durable binaries are
the SDK files attached to an explicitly tagged GitHub Release and its three GHCR images. Repository Actions
artifacts and logs use a 7-day retention window as a backstop.

## Cutting a release

1. At an owner-approved major milestone, open PR `dev → main` → **deep CI passes**.
2. Bump `__version__` (in the PR); merge to `main`.
3. Only after the owner explicitly approves a major milestone: `git tag vX.Y.Z && git push origin vX.Y.Z` → the release workflow builds + publishes the
   package **and** the three images. Verify after the run: `docker pull
   ghcr.io/mvalancy/moxie-robot-saver/supervisor:X.Y.Z` and, on the very first release, flip the
   three packages to public.
4. Reconcile the squash back into `dev`; leave no promotion PR open between milestones.

Build a package locally anytime: `cd mqtt && python -m build` → `dist/moxie_cloud_sdk-<version>.*`.

## Workflow files — templates and the installed copies

The three workflows are **installed** in `.github/workflows/` and mirrored as templates in
[`sim/ci/`](sim/ci/) (`ci.yml`, `ci-deep.yml`, `release.yml`). Edit the template, then sync the installed
copy in the same change (`cp sim/ci/<file> .github/workflows/<file>`); the AUDIT loop checks they're
identical. Pushing `.github/workflows/` needs a `workflow`-scoped token (the orchestrator session has one;
revoke it when the session ends). HIL against real infra uses repo **secrets** (gateway key, voice URL,
robot host) that the deep workflow reads — never committed.

---
📖 [Repo structure](STRUCTURE.md) · [Implementation plan](docs/architecture/implementation-plan.md)
