# SIL + static-site pytest automation

A comprehensive [Playwright](https://playwright.dev/python/) suite that drives the
whole static site in a real browser — every page at every standard resolution, and
the simulator through **all of its modes and controls**:

- **All pages × all resolutions** (`phone-portrait` → `ultrawide`): load clean, no
  console errors, no horizontal page scroll.
- **SIL**: click every expression chip (the 11 Eyeseme moods + sleep/thinking/blink);
  drag every motor slider; toggle **ALIVE**/liveness, axes, sound, heart-LED; run the
  speech path (pre-cached chip + free text); center-pose.
- **ALIVE loop**: on by default, stops on toggle-off, mirrors the panel checkbox, and
  a user-held joint is left alone.
- **Docs explorer**: full-text search filters, opening a hit, Mermaid renders.

## The non-browser tests in here

The directory has grown a second, larger family: plain pytest files that need no
browser at all and carry the hermetic suite CI actually runs.

```bash
.venv/bin/python -m pytest sim/tests -q -k "not test_sil and not test_docs" \
  --ignore=sim/tests/test_live_gateway.py      # the hermetic suite
```

- **`helpers_runtime.py`** — the shared harness for anything that drives a turn
  through the real `MoxieRuntime`: a `FakeClient` that records publishes,
  `make_runtime` / `drive_turn` / `drive_once`, and `assert_spec_response` (the
  RemoteChatResponse conformance check). Import this rather than growing a fifth
  private copy of it. It also owns `LatchClient` (a fake transport a test can *wait on*
  instead of sleeping) and `CountingSynth`.
- **`test_segment.py`** — the pure sentence segmenter (`moxie_sdk/segment.py`) a streaming
  brain talks through: boundaries, the same split whatever size the network cuts the
  stream into, decimals, abbreviations and initials, ellipses, the minimum chunk length,
  and the flush contract (the LAST sentence must still be buffered when the stream ends,
  so there is always a chunk left to close the turn with).
- **`test_streaming.py`** — streamed replies end to end: chunk numbering under one
  `event_id`, a one-sentence stream staying wire-identical to a plain reply, an action
  riding out on chunk 0, a late first token → filler, a mid-answer stall → a second filler
  and provably never a third, a newer turn cancelling a stream, a stream failing before a
  word (falls back to `respond`) or mid-answer (closes the sequence), `MOXIE_STREAMING=0`,
  LLMApp's own streaming path, and the SIL client joining the chunks of one turn.
- **`test_brain_latency.py`** — the background-inference + filler behavior: a fast brain
  still answers in exactly one `SUCCESS` chunk, a slow one speaks a filler
  (`REPLY_PENDING`, chunk 0) inside the budget and delivers the real line as chunk 1, a
  superseded turn's answer is dropped, fillers never repeat back-to-back, and both chunks
  get their own `CloudTTSResponse`. No sleeps: the fake brain blocks on an `Event` the
  test releases and the fake transport is a `Condition` the test waits on.
- **`test_fleet_config.py`** — the fleet-level default config (audit ADOPT #6): the pure
  precedence + deep-merge rule of `cloud_config.merge_config_layers`, the store's
  `fleet/config.json` record (and that a robot literally named `config` cannot collide
  with it), and the runtime seam — one fleet edit re-pushes **every** connected robot, a
  per-robot override still wins, and the status snapshot stays JSON-safe.
- **`test_console_roundtrip.py`** — the parent console ⇄ supervisor contract, driven
  in-process against a status-server double whose payload keys are diffed against the
  real runtime. Needs `fastapi` + `httpx`; skips cleanly without them (CI has neither).
- **Live tests** (`test_live_gateway.py`, `test_live_action_tags.py`,
  `test_live_content_e2e.py`) — real completions through the LLM gateway. They run
  only when `MOXIE_LLM_API_KEY` (or `LITELLM_MASTER_KEY`) is present, e.g. from the
  git-ignored `mqtt/.env`, and **skip** otherwise, so the hermetic run stays fast and
  CI stays green with no key. They find that file through
  `helpers_runtime.load_repo_dotenv()`, which looks in this tree and then in the **main
  checkout**, so the live tier runs from a `git worktree` too (before that it silently
  skipped there). To force the creds-free behavior locally — exactly what CI does — run
  the suite with `MOXIE_LLM_API_KEY= `. `test_live_action_tags.py` asserts a *rate* (2 of 3
  sampled turns) rather than a single sample, because the brain runs at temperature
  0.8 — see its docstring for the measured numbers.
  **In CI** all three run together in the deep tier's dispatch-only step
  (`gh workflow run ci-deep.yml --ref dev`) as one `pytest -q -ra` invocation against the
  repo secrets — see [`../ci/README.md`](../ci/README.md). That step *fails* on an empty
  `MOXIE_LLM_API_KEY` rather than skipping, because a green live run that tested nothing is
  the exact gap it exists to close. It is manual because it spends ≈12–13 real completions.
- **`test_live_talk_e2e.py` + `helpers_audio.py`** — the *voice* live tests: real Piper
  speech in, real Piper speech out, read back by real faster-whisper. Tier 1 round-trips
  Moxie's own voice through Whisper (and proves the built-in `ToneSynthesizer` would
  fail the same check, so the suite can never pass on the placeholder); tier 2 plays a
  child with a second voice, frames the audio as `zmqSTTRequest` protobuf and drives the
  whole loop through the real `MoxieRuntime` — `events/zmq` → transcript →
  `RemoteChatResponse` → `CloudTTSResponse` → transcribed back. `helpers_audio.py` holds
  the PCM maths (resample, spectral flatness, ZCR), the word-overlap scorer and the
  frame encoder. **Deliberately not in `requirements.txt`**: `piper-tts` and
  `faster-whisper` are heavy, so the file skips at collection when they (or the
  git-ignored `sim/tts/voices/*.onnx`, or a gateway key) are absent. To run it:
  `pip install piper-tts faster-whisper numpy`, then
  `python -m pytest sim/tests/test_live_talk_e2e.py -q -s` — add `MOXIE_VOICES_DIR=…`
  if the voices live outside this checkout (a git worktree starts without them).
  **In CI** it is opt-in on the same dispatch: `gh workflow run ci-deep.yml --ref dev -f
  voice=true` installs those three packages and fetches the voices with
  [`../ci/fetch_piper_voices.py`](../ci/fetch_piper_voices.py) (pinned `rhasspy/piper-voices`
  `v1.0.0` URLs, sha256-verified, cached, idempotent). That step fails unless ≥3 of its 4
  tests really passed — only the live-brain one may legitimately skip, when the gateway
  degrades to its canned fallback.

## Two rules that keep this suite hermetic and green

Both were learned from a red `dev` push (CI run 33624718428), and both are properties of
the *code under test*, not of the assertions:

- **Never assert on a live animation; assert on what the page recorded.** The mouth is
  driven by the audio envelope for the ~1 s an utterance lasts, so a test that samples
  `getMouthOpen()` has to catch it mid-open and loses that race on a loaded runner.
  `audio.js` therefore remembers the loudest frame of each cloud-TTS utterance and keeps
  it after playback ends — `moxieAudio.lastMouthPeak()` — so the assertion happens once
  the utterance is *over*. It is 0 when no PCM rendered and ~1.0 when it did, so it still
  fails loudly if the Web Audio graph breaks. Same idea as reading the whole speaking
  state from one atomic `wait_for_function` snapshot instead of several round-trips.

- **A test that drives a brain with a fake must not need the real SDK.** `LLMApp` (like
  `OpenAIVoiceSynthesizer`) takes the OpenAI-compatible `client=` seam and only imports
  `openai` when it has to build one itself, so the tag/streaming tests construct it with
  a fake and run on a bare interpreter. Reserve `pytest.importorskip("openai")` for tests
  that genuinely talk to a gateway (`test_live_*.py`).

## Run


```bash
sim/tests/run.sh              # sets up the venv on first run, then runs everything
sim/tests/run.sh -q -k alive  # pass any pytest args through
```

It reuses the locally-cached Chrome (`~/.cache/puppeteer/...`, the same binary the
node/puppeteer tests use), so nothing needs downloading. If no Chromium/Chrome is
available, the suite **skips cleanly** (exit 0) — like the node browser tests — so CI
stays green without a browser.
