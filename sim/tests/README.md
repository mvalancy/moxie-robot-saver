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

  Four more pieces landed with the v0.7.0 integration pass, each replacing a hand-rolled
  copy: **`free_port()`** (bind `:0` — never a hard-coded 8930/1883, which a lab machine
  and sibling agents already hold), **`status_server(rt)`** which starts the runtime's
  *real* `_start_status_server` on one and hands back its base URL, **`http_json(url, …)`**
  for talking to it, and **`loopback(rt, vm)`** — the two-subscriber in-process broker that
  lets a `sim/virtual_moxie.py` robot and a real runtime exchange bytes on the real topics
  with no mosquitto and no sleeps. `make_runtime` also takes `store=` now, so a test that
  writes durable state points it at a `tmp_path` instead of the developer's data dir.
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
- **`test_memory_view.py`** — the pure transform behind the console's 🧠 What Moxie
  remembers card (`moxie_server/fleet.py::normalize_memory`): the runtime's namespaced
  `/memory` payload flattened into dated rows per activity, newest first, with counts —
  plus the tolerance that matters on a parent's screen (a partial namespace, a list a
  module invented, a raw `memory.json` off disk, and a supervisor that is down).
- **`test_broker_acl.py`** — broker hardening P0
  ([`security-broker-auth.md`](../../docs/architecture/backlog/security-broker-auth.md) §2), pure: no
  broker, no Docker. Half is `mqtt/moxie_sdk/broker_acl.py::render_acl` — the permit-derived ACL that is
  generated now and **inert until P1** — asserted for a byte-stable golden shape, one `user d_<uuid>`
  block per permitted device, and the property the whole floor rests on: **no bare `topic` grant before
  the first `user` block**, so an anonymous client's only reach is its own `%c` subtree (plus the
  injection case: a device id carrying a newline cannot forge an ACL line). Half reads the **shipped**
  `mqtt/broker/` files, and catches the two ways this slice can silently become a no-op — an ACL that
  stops being loaded, and a `user` block on a listener with no `password_file`, where mosquitto matches a
  username nobody verified. The rest is the supervisor credential: `config.broker_credentials()`
  precedence (literal > file > anonymous, and a username without a password is *not* credentials) and
  `MoxieRuntime._build_client` actually calling `username_pw_set`. The end-to-end proof against a real
  broker is [`../run_acl_proof.sh`](../run_acl_proof.sh).
- **`test_compose.py` + `helpers_compose.py`** — the one-command stack, asserted with
  PyYAML and never Docker. Half is shape (`docker-compose.yml` declares the three
  services plus the cert one-shot, healthchecks, restart policies, named volumes,
  strictly opt-in profiles, a documented and secret-free `.env.example`); half is
  **parity** between `docker-compose.yml` and the self-contained
  `docker-compose.images.yml`. The images file cannot reference this repo — an owner
  downloads it alone — so it repeats the supervisor's `environment:` block and inlines
  `mqtt/broker/compose-mosquitto.conf`, and both copies have to be re-proven copies:
  identical `MOXIE_*` env for `supervisor`/`console`/`certs` (same keys, same
  `${VAR:-default}`), the inlined broker config diffed against the file *and* checked for
  the un-escaped `$` that a pure diff is blind to, and the same services, healthchecks,
  `depends_on` conditions, published-port defaults and volume paths. This is a regression
  suite: v0.6.0's promotion stalled because the pairing-gate knob
  `MOXIE_ALLOW_UNVERIFIED_BOTS` reached only one of the two files, and until now only the
  deep tier's PR-to-main docker smokes could see that. Every guard is paired with
  **negative tests** — tiny in-memory compose pairs carrying exactly one injected drift —
  so the suite proves the guards still bite, not merely that they pass.
- **`test_schedule_sil_e2e.py`** — the adaptive day plan end to end: the parent writes a
  bedtime and a `ParentRequest` over the **real** `POST /config?scope=fleet`, a SIL robot
  pulls its day over the **real** `CloudQuery` round trip, and the parent reads the "why
  this activity today" lines back over the **real** `GET /schedule`. The claim that binds
  them is the one nothing else asserted: *the ids the robot was served are exactly the ids
  the parent is shown an explanation for* — an audit trail describing a different day than
  the robot got would be worse than none. Also: nothing is planned inside bedtime (and the
  day really is truncated by it), the parent's request is pinned and says so, finished FTUE
  never comes back, a module quit an hour ago is not re-offered, and a reported
  `mentor_behavior` reaches the store and changes the next plan. Hermetic — the loopback
  above stands in for the broker, the store is a `tmp_path`, and every clock window is
  computed relative to *now*, so it never depends on the hour it runs at.
- **`test_package_contents.py`** — what `pip install moxie-cloud-sdk` actually gets, which
  no other test can see because every other test runs against the source tree: every
  package on disk is in `[tool.setuptools] packages` (add `moxie_sdk/telehealth/`, forget
  the line, and it ships absent), every non-code file inside a package is covered by a
  `[tool.setuptools.package-data]` glob (`moxie_sdk = ["*.json"]` covers exactly one
  package — a JSON under `apps/` or `content/` matches nothing and is dropped silently),
  the version has one source, and **no module needs an optional backend to import** — that
  last one asserted in a subprocess where `openai`/`numpy`/`jinja2`/`piper`/`faster_whisper`
  are made unimportable, so the full-fat venv catches what playbook rule 9 says the fast
  tier otherwise discovers as a red push.
- **`test_live_gateway_turn_e2e.py` + `helpers_stack.py`** — ONE live turn on the
  *assembled appliance*, which is the combination nothing else covered:
  `test_live_gateway.py` proves the brain, `test_live_gateway_tts.py` proves the voice but
  builds its synthesizer in-process, and `sim/run_smoke.sh` puts a real robot on a real
  broker with the echo app and the tone beep. `helpers_stack.py` boots the real thing —
  mosquitto on a free port (binary, else docker) and `mqtt/run.py` in a subprocess with its
  own scratch `MOXIE_DATA_DIR` — and the SIL robot takes one turn with `MOXIE_APP=llm` +
  `MOXIE_VOICE_BASE_URL`. Budget: **1 completion + 1 `/audio/speech`**, one module-scoped
  fixture every test reads (the filler timer is put out of reach so a slow brain cannot
  buy a second voice call). The audio assertion is spectral flatness, not sample rate:
  `ToneSynthesizer` also emits 22050 Hz mono, and the creds-free guard in the same file
  proves the tone fails the check, so a green cannot mean "the standby spoke".
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
- **`test_live_gateway_tts.py`** — the *gateway voice* live tests (live since 2026-09-02).
  `MOXIE_VOICE_BASE_URL` + `MOXIE_VOICE_MODEL` is the whole switch, so the file builds its
  synthesizer with `config.build_synthesizer()` and then makes the gateway prove it is
  speaking: four `/audio/speech` calls — `piper-amy` as WAV (unwrapped to the header's own
  22050 Hz and transcribed back at word overlap ≥ 0.7), `piper-ryan` to show the model *is*
  the voice switch, one `pcm` call, and one deliberately-unknown model to prove the turn
  downgrades to the standby voice instead of going silent. The tier-1 anti-tone guard is
  the same one `test_live_talk_e2e.py` uses, so this suite cannot pass on the placeholder
  beep either. Needs `faster-whisper` + `numpy` (not `piper-tts`, and no 63 MB voice file —
  the gateway does the speaking); skips cleanly without them or without a voice URL. **In
  CI** it rides in the same creds-only dispatch step as the three suites above, which also
  fails on an empty `MOXIE_VOICE_BASE_URL`.
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

  The **queue** is the same kind of live thing, and cost a second flake (run
  `33629395950`, ~50% on `dev`): `test_cloud_tts_chunks_play_in_order_then_stop` sampled
  `moxieAudio.ttsPending()` at the instant `isSpeaking()` first went true, and on a fast
  runner four tenths of a second of audio had already drained into the gap, so the chunks
  that demonstrably *had* queued read as `pending: 0`. The cure is the same shape:
  `moxieAudio.lastPlaybackStats()` → `{event_id, chunks_played, order, max_pending}`,
  recorded as playback happens and frozen when it ends, so the test waits for the
  utterance to finish and then asserts three stronger facts (all three chunks played,
  in `chunk_num` order, having queued at least one deep). The test proves the assertion
  no longer depends on timing by repeating it with 10 ms chunks injected in one
  synchronous round trip — they drain faster than any observer could look, and the
  recorded stats still report the same thing.

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
