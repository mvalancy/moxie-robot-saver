# `sim/ci/` — GitHub Actions workflow templates (+ the live-tier helper)

These are the **source of truth** for our CI, mirrored to the installed copies under
`.github/workflows/`. **Edit the file here, then sync in the SAME commit** — the two must stay
byte-identical or `sim/tests/test_ci_workflows.py` reddens, which is why a workflow change must
never be split across commits that could be dropped separately.

> **The session token CAN push `.github/workflows/` (verified 2026-09-03, PR #79).** This note
> used to say it lacked `workflow` scope and that the owner copied the files across by hand.
> That stopped being true, and the stale claim cost real work: an agent isolated a workflow
> commit so it could be dropped whole, and a later one flagged its CI edit as unpushable. Treat
> CI tier changes as ordinary work. Verify after pushing with
> `git diff --quiet origin/<branch>:sim/ci/<file>.yml origin/<branch>:.github/workflows/<file>.yml`.

```sh
cp sim/ci/ci.yml sim/ci/ci-deep.yml sim/ci/release.yml .github/workflows/
```

| File | Tier | Trigger | What it proves |
|---|---|---|---|
| **`ci.yml`** | fast (dev) | push `dev`, PR → `dev` | doc/protocol guards, SIL smoke, the hermetic unit/cloud suite (~5 min) |
| **`ci-deep.yml`** | deep (main) + HIL | PR → `main`, **manual dispatch** | everything above, plus the packaged build, the compose stack, and the **live tiers** below |
| **`release.yml`** | release | tag `v*` | sdist+wheel, version==tag, GitHub Release |

## What the fast tier's `sil` job actually runs (measured 2026-09-04)

Recorded because it was assumed twice and is cheap to check:

| Step | Selection | Collected |
|---|---|---|
| *Hermetic pytest, EARLY* | `-k "not test_sil and not test_docs" --ignore=test_live_gateway.py` | **5,077** |
| *SIL + static-site pytest/Playwright suite* | `pytest sim/tests -q` (unfiltered) | **5,187** |

So the job runs the hermetic suite **twice**, and the second run re-executes 5,077 tests
to reach exactly **110 new ones**: the 106 `test_sil*` / `test_docs*` tests the `-k`
deselects, plus the 4 in `test_live_gateway.py` the `--ignore` drops. Every browser-backed
*pytest* test in the repo — the ones taking conftest's `page` / `browser` fixtures, i.e.
`test_sil.py` and `test_sil_child_voice.py` — is inside those 106, so splitting the job on
that line is mechanically available.

**It is deliberately not split.** The duplication is real waste, but it is not free to
remove: `sim/tests/test_ci_workflows.py::test_the_fast_tier_runs_the_whole_pytest_suite`
requires one *unfiltered* fast-tier invocation, and it was written after the #43–#46
post-mortem precisely to forbid a `-k`-filtered tier. What it defends is that somewhere in
the tier every test runs under the fullest dependency set, which is what turns a future
`importorskip` into a red rather than a skip. The two intermittent reds this measurement
was taken for turned out to be latent races (see the `test_sil_handshake.py` and
`test_clean_shutdown.py` docstrings) and were fixed at the source, so paying a proven
invariant for ~2 minutes would be buying nothing.

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
