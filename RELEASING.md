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
- **Build loops:** commit to `dev` (the integration RC branch); a standing **`dev → main`** PR shows
  rolling CI. Larger/riskier work still uses a `feat/*` → `dev` PR.
- **Promotion:** when `dev` is a stable RC, merge the standing dev→main PR into `main` (deep CI must
  pass), then tag. **Resolve its number dynamically** — it changes on every promotion — with
  `bash scripts/standing-pr.sh` (never hardcode `#1`).
- **After a promotion (squash) — reconcile `dev`:** a squash-merge gives `main` one commit that shares
  **no ancestry** with `dev`'s granular history, so both branches look like they independently "added"
  the same files — the recreated standing PR reads **CONFLICTING**, not empty. Fix it in two steps,
  right after the squash:
  1. `gh pr create --base main --head dev` — recreate the standing PR.
  2. On `dev`: `git fetch origin && git merge origin/main -X ours --no-edit` then push. `dev` is a
     superset of `main`'s squash, so this changes **no content** (verify: `git diff <pre> HEAD` is
     empty) — it only re-links history. The standing PR then diffs cleanly (only genuinely-new work).

  (Alternative: promote with a **merge commit** instead of squash — `main` keeps full history and no
  reconcile is needed. We use squash for a clean one-commit-per-release `main`, and pay the reconcile.)

## CI tiers

| Tier | Workflow | Trigger | Runs |
|---|---|---|---|
| **Fast** (dev) | `.github/workflows/ci.yml` | push `dev`, PR → `dev` | docs/protocol guards + SIL smoke + unit/cloud tests (~5 min) |
| **Deep** (main) | `.github/workflows/ci-deep.yml` | PR → `main` | full suite + **HIL sim** (hardware-in-the-loop: a virtual robot end-to-end, later a real robot on a self-hosted runner) |
| **Deep — live** | same, `workflow_dispatch` | `gh workflow run ci-deep.yml --ref dev` | the above **+ the live gateway suites** (`test_live_gateway` · `test_live_action_tags` · `test_live_content_e2e`) on the real brain via repo secrets. **Spends ≈12–13 real gateway completions** — hence manual, never on a PR. Fails (not skips) if the secret is empty. |
| **Deep — live voice** | same, dispatch input | `gh workflow run ci-deep.yml --ref dev -f voice=true` | the above **+ `test_live_talk_e2e`**: real Piper speech ⇄ real faster-whisper. Installs the voice deps and fetches 2 × 63 MB pinned Piper voices (cached); ~1 more completion. Fails if fewer than 3 of its 4 tests actually ran. |
| **Release** | `.github/workflows/release.yml` | tag `v*` | build sdist+wheel, verify version==tag, GitHub Release (+ index publish when configured) |

## Version numbering (semver)

Single source: `mqtt/moxie_sdk/__init__.py` `__version__`; `pyproject.toml` reads it.

- **`X.Y.Z`** — MAJOR.MINOR.PATCH. Pre-1.0 (`0.Y.Z`): `Y` = features (breaking allowed), `Z` = fixes.
- **Release candidates** on `dev`: `X.Y.Z-rc.N` (tag `vX.Y.Z-rc.N` for a pre-release).
- **Release** on `main`: `X.Y.Z` (tag `vX.Y.Z`). The release workflow **fails if `__version__` ≠ the tag**.
- Bump `__version__` in the `dev → main` promotion PR: `Z` for fixes, `Y` for a milestone (e.g. M-set complete).

## Cutting a release

1. `dev` green as an RC → open/refresh PR `dev → main` → **deep CI passes**.
2. Bump `__version__` (in the PR); merge to `main`.
3. `git tag vX.Y.Z && git push origin vX.Y.Z` → the release workflow builds + publishes.
4. Recreate the standing `dev → main` PR (`gh pr create --base main --head dev`).

Build a package locally anytime: `cd mqtt && python -m build` → `dist/moxie_cloud_sdk-<version>.*`.

## ⚠️ Workflow install (owner, one-time — needs a `workflow`-scoped token)

`.github/workflows/` can't be pushed from the build session (token lacks `workflow` scope). Install-ready
templates live in [`sim/ci/`](sim/ci/): **`ci.yml`** (fast, dev tier), **`ci-deep.yml`** (deep + HIL, main
tier), **`release.yml`** (tags). Install:
```sh
cp sim/ci/ci.yml sim/ci/ci-deep.yml sim/ci/release.yml .github/workflows/ && \
git add .github/workflows/ && git commit -m "ci: fast(dev)+deep(main,HIL)+release tiers" && \
git -c http.extraheader="AUTHORIZATION: basic $(printf 'x-access-token:TOKEN' | base64 -w0)" push   # then revoke
```
HIL against real hardware/servers uses repo **secrets** (gateway key, voice URL, robot host) the deep
workflow reads — never committed. Until installed, the live `ci.yml` gates PRs to `main` and locals build packages.

---
📖 [Repo structure](STRUCTURE.md) · [Implementation plan](docs/architecture/implementation-plan.md)
