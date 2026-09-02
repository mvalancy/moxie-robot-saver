# `sim/ci/` — GitHub Actions workflow templates (+ the live-tier helper)

These are the **source of truth** for our CI. They are *templates*: the build session's token
lacks `workflow` scope, so it cannot push under `.github/workflows/` — the owner copies them
across (see [`../../RELEASING.md`](../../RELEASING.md)). **Edit the file here, then sync.**

```sh
cp sim/ci/ci.yml sim/ci/ci-deep.yml sim/ci/release.yml .github/workflows/
```

| File | Tier | Trigger | What it proves |
|---|---|---|---|
| **`ci.yml`** | fast (dev) | push `dev`, PR → `dev` | doc/protocol guards, SIL smoke, the hermetic unit/cloud suite (~5 min) |
| **`ci-deep.yml`** | deep (main) + HIL | PR → `main`, **manual dispatch** | everything above, plus the packaged build, the compose stack, and the **live tiers** below |
| **`release.yml`** | release | tag `v*` | sdist+wheel, version==tag, GitHub Release |

## The live tiers in `ci-deep.yml`

Everything else in CI is hermetic. Two steps are not, and both are **`workflow_dispatch`
only** — never on a PR. That guard is deliberate twice over: GitHub withholds secrets from
fork PRs anyway, and **a dispatch spends real gateway calls**, so it stays a thing a human
asks for rather than something every push pays for.

```sh
gh workflow run ci-deep.yml --ref dev                  # creds-only live tier
gh workflow run ci-deep.yml --ref dev -f voice=true    # + the live VOICE tier
```

- **Live gateway tier** (always, on dispatch) — `test_live_gateway.py`,
  `test_live_action_tags.py` and `test_live_content_e2e.py` in one `pytest -q -ra` run,
  against the real LiteLLM gateway via the `MOXIE_LLM_API_KEY` / `MOXIE_LLM_BASE_URL` /
  `MOXIE_LLM_MODEL` repo secrets. About a dozen (**≈12–13**) real completions per dispatch.
- **Live voice tier** (`-f voice=true`) — `test_live_talk_e2e.py`: real Piper speech in,
  real Piper speech out, read back by real faster-whisper. Installs `piper-tts`,
  `faster-whisper`, `numpy` and fetches the two voices (below); **~1 completion**.

### Why both steps *fail* instead of skipping

Every live test is written to **skip** cleanly without credentials — correct for the
hermetic tier, and exactly wrong on a dispatch: a run where the secret is empty, or where
piper/whisper/the voices never loaded, would report a green "live" job that proved nothing.
So the creds step fails when `MOXIE_LLM_API_KEY` is empty, and the voice step fails unless
at least **3 of its 4 tests actually passed** (only the live-brain one may legitimately skip,
when the gateway degrades to its canned fallback). Both write a counts + skip-reason block to
the job summary, so "what did this dispatch really run" is answerable without opening logs.

## `fetch_piper_voices.py`

`sim/tts/voices/*.onnx` is git-ignored (63 MB per voice), so a runner — like a fresh clone or
a `git worktree` — has neither, and the voice tier would skip. This fetches them:

- **pinned** to the `v1.0.0` tag of the official `rhasspy/piper-voices` repository, never a
  moving branch (a changed voice would silently move the acceptance thresholds in
  `test_live_talk_e2e.py`);
- **sha256- and size-verified**, downloading via a `.part` file so an interrupt cannot leave a
  truncated model that a later run treats as cached;
- **idempotent** — an already-correct file is never re-downloaded, so a warm `actions/cache`
  hit (keyed on that same pinned release) costs nothing, and so does running it in a checkout
  that already has the voices;
- **stdlib only**, so it runs before any `pip install`.

```sh
python3 sim/ci/fetch_piper_voices.py            # into sim/tts/voices/
python3 sim/ci/fetch_piper_voices.py --check    # verify only; exit 1 if anything is missing
```

---
📖 [Releases & CI tiers](../../RELEASING.md) · [SIL & CI/CD](../../docs/architecture/sil-and-cicd.md) · [The test suites](../tests/README.md)
