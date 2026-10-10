# `sim/tests/` — the pytest suite

Two families in one directory: a large **hermetic** pytest suite (no browser, no network, no
credentials; what CI gates on) and a small **Playwright** suite that drives the static site in
a real Chromium. `test_live_*.py` files spend real gateway calls and skip without a key.

## Run

```bash
sim/tests/run.sh                  # builds .venv from requirements.txt on first run, then runs everything
sim/tests/run.sh -q -k alive      # any pytest args pass through

# the hermetic suite, as CI runs it (sim/ci/ci.yml, ci-deep.yml)
python3 -m pytest sim/tests -q -k "not test_sil and not test_docs and not test_live" \
  --ignore=sim/tests/test_live_gateway.py
```

`run.sh` reuses the cached Chrome under `~/.cache/puppeteer`; with no Chrome the browser tests
skip cleanly. Add `not test_live` locally: a key in `mqtt/.env` makes the live suites spend money.

## Support files

- [`conftest.py`](conftest.py) — serves `sim/web` via `sim/serve.py` on a free port, provides the
  Playwright `browser`/`page` fixtures, points `MOXIE_DATA_DIR` at a temp dir, sets the dotenv fence,
  and hides live credentials from hermetic tests (`hermetic_tier_sees_no_credentials`).
- [`run.sh`](run.sh) — one-shot venv + pytest runner; re-installs when either requirements file changes.
- [`requirements-hermetic.txt`](requirements-hermetic.txt) — the ONE declaration of test deps
  (pulls in `mqtt/requirements.txt` and `server/requirements.txt`); every CI job running
  `pytest sim/tests` installs it.
- [`requirements.txt`](requirements.txt) — the hermetic file plus `playwright`, nothing else
  (`test_ci_workflows.py` fails if they differ by anything more).
- [`helpers_runtime.py`](helpers_runtime.py) — drive a turn through the real `MoxieRuntime`:
  `FakeClient`, `LatchClient`, `make_runtime`/`drive_turn`, `assert_spec_response`, `free_port`,
  `status_server`, `loopback` (in-process broker between `sim/virtual_moxie.py` and a runtime),
  `deliver` (one message into `_on_message`, a broker log line included), the binary
  `commands/zmq` readers `split_zmq_frame` / `parse_zmq_frame` with `toolkit_pb2` (the committed
  `tools/robot-toolkit` pb2 oracles), `load_repo_dotenv` / `LIVE_KEYS`.
- [`helpers_stack.py`](helpers_stack.py) — boot the real broker + `mqtt/run.py` on free ports.
- [`helpers_console.py`](helpers_console.py) / [`helpers_console_supervisor.py`](helpers_console_supervisor.py) —
  import the parent console in-process; a fake supervisor status server for `test_console_*.py`.
- [`helpers_content.py`](helpers_content.py), [`helpers_ext.py`](helpers_ext.py) — builders for the
  `test_content*.py` and `test_ext*.py` suites.
- [`helpers_audio.py`](helpers_audio.py) — PCM maths, spectral flatness (numpy and stdlib twins),
  word overlap, `zmqSTTRequest` framing; numpy is optional here on purpose.
- [`helpers_compose.py`](helpers_compose.py) — parity helpers for the two compose files, and readers that
  resolve a file as `docker compose up` would (where each published port listens).
- [`helpers_qr_matrix.py`](helpers_qr_matrix.py) — decode a payload back out of a QR module matrix.
- [`helpers_web.py`](helpers_web.py) — `script_group("bridge"|"voice")`, the pytest twin of
  `sim/bridge_harness.mjs::scriptGroup`.
- [`helpers_route.mjs`](helpers_route.mjs) — run one real Pages Function on one request (used by
  `test_live_hosted_ears.py`).
- [`helpers_shared_ceilings.mjs`](helpers_shared_ceilings.mjs) — node entry for the shared-tier
  ceilings sections (run by `test_shared_ceilings.py`).
- [`helpers_probe_budget_loopback.mjs`](helpers_probe_budget_loopback.mjs) — child-process
  loopback HTTP helper for `sim/test_mode.mjs`.

## Subfolders

- [`data/`](data/README.md) — `ext_conformance.json`, the sandboxed-extension goldens.
- [`goldens/`](goldens/README.md) — recorded wire/markup goldens and one real-voice WAV.
- [`edge/`](edge/README.md) — sections of the Pages Functions node suites (`sim/test_demo_proxy.mjs` etc.); `.mjs`, never collected by pytest.
- [`hosted_mic/`](hosted_mic/README.md) — modules behind `node sim/check_hosted_mic.mjs`.

## Test groups

| Prefix / files | Covers |
|---|---|
| `test_sil.py`, `test_sil_child_voice.py` | Playwright: every page at every resolution, expression chips, motor sliders, ALIVE loop, speech path; the child's clips reaching `ctx.destination` |
| `test_sil_*.py` (others) | Real mosquitto + supervisor: durable telemetry across a restart, SUBACK handshake both ends, performance fields on the wire, presence, brains/extensions |
| `test_runtime_turn`, `test_streaming`, `test_segment`, `test_brain_*`, `test_brains`, `test_backoff`, `test_gateway_timeouts`, `test_connect_readiness`, `test_connection_resilience`, `test_clean_shutdown` | The supervisor turn loop, streaming chunks, fillers, bounded gateway calls and the standby's retry window, reconnects |
| `test_action_*`, `test_actions_reach_the_robot`, `test_webhook_actions`, `test_launch_*` | `response_actions` from brain to robot; launch cards and sheet |
| `test_content*`, `test_render_*`, `test_automarkup`, `test_performance`, `test_faces` | Content packs, template sandbox and parity, markup floor, behavior planner |
| `test_ext*.py` | Sandboxed extensions: escapes X1–X12, conformance T1–T18, `act`/`subscribe` |
| `test_ext_say_tags.py` | A pack's spoken line acts only on the action tags written whole in its rule, and the review names them: the invariant as a property over random programs and run-time inputs, every way of building a tag, the report, shipped behaviour unchanged; a `say`'s markup or a `markup` statement reaches the robot with no tag of ours and nothing the catalogue's check refuses, in time linear in the markup; a conversation's opener acts only on the tags written whole in the alternative said, named in its own review row (a property over random opener templates) |
| `test_memory*`, `test_telemetry*`, `test_store*`, `test_transcript_memory_policy`, `test_soak_accounting` | Durable store, roll-up repair, erase and logging policy |
| `test_notify_history` | The robot's notify is the record, not a second copy: one turn plus its notify is two lines in RAM and on disk, per-chunk or per-event reports in any order re-assemble a streamed answer, a filler or queued hello echoed back is never history, a cut-off answer becomes what Moxie got through, a module's own line is appended and joined as OpenMoxie joins it, the child's speech windows answered as separate turns and reported together (or a turn reported late, after the next prompt) add no line and an earlier window joins the child's line, a report the proto cannot carry or the reconcile cannot handle fails alone (the answer still spoken, the turn still remembered), a never-notifying robot's history is byte-identical (golden), and the brain and the goodbye summary read each line once |
| `test_schedule*` | Day planner, parent requests, the "why" view, SIL end to end |
| `test_telehealth*`, `test_presence*`, `test_fleet*`, `test_cloud_config`, `test_device_permits`, `test_roster`, `test_why_no_config` | Config push, fleet defaults, pairing gate, telehealth |
| `test_console_*`, `test_parent_api`, `test_brain_console` | Parent console ⇄ supervisor contract (need `fastapi` + `httpx`) |
| `test_tts`, `test_stt*`, `test_voice_*`, `test_sim_tts_playback`, `test_speech_guard` | Voice engines, gateway STT, the tone-vs-speech guard |
| `test_honest_ears` | What the ears refuse to hear: digital silence and sub-120 ms clips reach no engine, a sound label alone is silence, Whisper's "Bye." on room tone is dropped, local whisper's `vad_filter`, the kill switch |
| `test_config_*`, `test_assemble`, `test_dotenv_cannot_perturb_the_suite`, `test_env_hygiene_live_suites`, `test_no_deployment_defaults` | Config precedence and the dotenv fence |
| `test_compose`, `test_broker_acl`, `test_package_contents`, `test_render_container_deps` | Compose parity and what each port is published on, broker ACL, what the wheel ships |
| `test_ci_*`, `test_clock_dependence`, `test_mutation_tables`, `test_readiness_guards_are_checked`, `test_harness_readiness`, `test_node_global_stubs`, `test_page_teeth_slow_mode`, `test_promotion_guard` | Guards on CI itself: workflows mirror `sim/ci/`, every `sim/test_*.mjs` is run by a tier, reviewed wall-clock reads |
| `test_csp_hashes`, `test_no_offsite_images`, `test_shared_ceilings`, `test_sim_client_parity`, `test_safety`, `test_sdk` | Static-site CSP, images, shared rate-limit tier, SDK and safety floor |
| `test_hosted_docs_truth` | The hosted demo's docs say what its code does: no retired claim made as a live statement (a global spend ceiling, the kill switch as the fastest response), the deploy guide's modes are `modeOf`'s, nothing cites a deleted file, every default a doc states for a per-visitor window (chat, speech, transcribe) is `env.js`'s |
| `test_live_*.py`, `test_smoke_live_brain` | Real gateway completions, TTS, STT, hosted ears, voice round trip; skip without credentials |
| `test_robot_lifecycle` | Unpair and factory reset: account record, permit revoke, voided pairing codes, the `restore_factory` code (need `fastapi` + `httpx`) |
| `test_robot_claim`, `test_wifi_first_qr` | Bench-day pairing: the Wi-Fi-only first code, Add to my account (the claim), `/local/state`'s lists of robots, and one robot per account on Simulate robot scan too (need `fastapi` + `httpx`); `test_sil_robot_claim` (SIL group) runs the claim against real mosquitto and the real supervisor |
| `test_child_name`, `test_child_name_safety`, `test_console_child_name` | The child's name Moxie says: the one name rule (shared with Try it: its shape, NFC, Moxie's safety rules), every place Moxie names the child, a hello the safety rules block, a write for a robot that is away, K4's fail-closed race, where the name never goes (the log and the feed say `[child]`), a revoke that takes it off; the console's sends, clears, a name refused before it is saved, the unpair retried by a revoke, and who may read it back (the console file needs `fastapi` + `httpx`) |

Every file's docstring states what it proves and, where relevant, its mutation-check companion
in [`../tools/`](../tools/README.md).

## Gotchas

- **Dotenv fence.** `conftest.py` sets `MOXIE_SKIP_DOTENV=1` before collection so `mqtt/.env`
  cannot change what the suite sees. Override with `MOXIE_SKIP_DOTENV=0` (as the deployment sees
  it) or `MOXIE_DOTENV=<file>` (a fixture). `helpers_runtime.load_repo_dotenv` (live tier) loads
  only `LIVE_KEYS` and also looks in the main checkout, so live tests run from a worktree.
  `test_dotenv_cannot_perturb_the_suite.py` guards both.
- **Force creds-free locally:** `MOXIE_LLM_API_KEY= pytest sim/tests`.
- **Live tier in CI** is dispatch-only: `gh workflow run ci-deep.yml --ref dev` (add
  `-f voice=true` for `test_live_talk_e2e.py`, which fetches voices with
  [`../ci/fetch_piper_voices.py`](../ci/fetch_piper_voices.py)). It fails, not skips, on an empty
  `MOXIE_LLM_API_KEY` / `MOXIE_VOICE_BASE_URL`. See [`../ci/README.md`](../ci/README.md).
- **Local voice tests:** `pip install -r sim/tests/requirements.txt piper-tts faster-whisper`, then
  `python -m pytest sim/tests/test_live_talk_e2e.py -q -s`; set `MOXIE_VOICES_DIR` if the
  `.onnx` voices are outside this checkout. `piper-tts`/`faster-whisper` are the only
  `DELIBERATELY_OPTIONAL` deps.
- **Hosted ears:** `test_live_hosted_ears.py` tier B needs `MOXIE_DEMO_ORIGIN` (no host is
  hard-coded); `MOXIE_EARS_WAV=<file>` reuses earlier audio.
- **Goodbye action-tag campaign:** use [`../tools/run_live_action_tags.sh`](../tools/run_live_action_tags.sh)
  (counts-only summary; `--moxie-campaign-state-file` is its pytest option).
- **Undeclared binaries are refused:** tests may spawn only `mosquitto`, `docker`, `node`, `git`,
  `bash` (`test_speech_guard.py::DECLARED_BINARIES`). No `ffmpeg`.
- **Assert on what the page recorded, not a live animation:** use `moxieAudio.lastMouthPeak()` and
  `lastPlaybackStats()` rather than sampling the mouth or queue mid-utterance.
- **Fake brains need no SDK:** `LLMApp` and `OpenAIVoiceSynthesizer` take a `client=` seam; keep
  `importorskip("openai")` for `test_live_*.py`.
- **Never hard-code ports** (1883/8930): use `helpers_runtime.free_port()`.
- A new `sim/test_*.mjs` needs a step in `sim/ci/ci.yml` (`test_ci_test_coverage.py`); a new
  `sim/tests/test_*.py` is collected automatically.

---
📖 [sim](../README.md) · [Back to top](../../README.md)
