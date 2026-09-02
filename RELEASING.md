# 🚀 Branching & releases

## Branches

| Branch | Role |
|---|---|
| **`main`** | Release-quality. Every commit is CI-green and tag-able. Protected in spirit — changes arrive via PR from `dev`. |
| **`dev`** | Active development / integration. The autonomous build loops commit here. |

**Flow:** loops build on `dev` → when an increment is stable, a **PR `dev → main`** runs CI (the
`pull_request` trigger) → merge on green → optionally **tag** `main` to cut a release.

> Feature work can branch off `dev` (`feat/…`) and PR back into `dev`; `dev → main` is the promotion gate.

## Versioning

Single source of truth: `mqtt/moxie_sdk/__init__.py` `__version__` (semver). `mqtt/pyproject.toml`
reads it dynamically. Bump it in the `dev → main` release PR.

## The package

`mqtt/` is an installable package — **`moxie-cloud-sdk`** (the clean-room robot-cloud SDK):

```sh
cd mqtt && python -m build          # → dist/moxie_cloud_sdk-<version>.{tar.gz,whl}
pip install "moxie-cloud-sdk[all]"  # extras: llm / stt / content / all
```

Optional backends are extras so the SDK imports + unit-tests with no heavy deps:
`llm` (openai), `stt` (faster-whisper + numpy), `content` (jinja2).

## Cutting a release

1. Open a PR `dev → main`; bump `__version__` in it; let CI go green; merge.
2. Tag `main`: `git tag v<version> && git push origin v<version>`.
3. The **release workflow** (`.github/workflows/release.yml`) fires on the tag: builds the sdist+wheel,
   attaches them to a GitHub Release, and (when configured) publishes to the package index.

## ⚠️ Workflow install (owner, one-time — needs a `workflow`-scoped token)

`.github/workflows/` can't be pushed from the build session (the token lacks `workflow` scope). Two
install-ready templates live in [`sim/ci/`](sim/ci/):

- **`sim/ci/ci.yml`** — the current CI, **plus** it should trigger on `push: [dev]` so dev work runs CI
  directly (the installed copy currently only triggers on `main` + PRs). Re-copy to
  `.github/workflows/ci.yml` after adding the `dev` trigger.
- **`sim/ci/release.yml`** — the release workflow (build + attach on a `v*` tag). Copy to
  `.github/workflows/release.yml`.

Install with a `workflow`-scoped token:
```sh
cp sim/ci/ci.yml sim/ci/release.yml .github/workflows/ && \
git add .github/workflows/ && git commit -m "ci: dev trigger + release workflow" && \
git -c http.extraheader="AUTHORIZATION: basic $(printf 'x-access-token:TOKEN' | base64 -w0)" push
```
(then revoke the token). Until installed, releases can still be built locally (`python -m build`).

---
📖 [Repo structure](STRUCTURE.md) · [Implementation plan](docs/architecture/implementation-plan.md)
