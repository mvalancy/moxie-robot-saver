# 🧪 `sim/tests/` — the pytest suite

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
  `load_repo_dotenv` / `LIVE_KEYS`.
- [`helpers_stack.py`](helpers_stack.py) — boot the real broker + `mqtt/run.py` on free ports.
- [`helpers_console.py`](helpers_console.py) / [`helpers_console_supervisor.py`](helpers_console_supervisor.py) —
  import the parent console in-process; a fake supervisor status server for `test_console_*.py`.
- [`helpers_content.py`](helpers_content.py), [`helpers_ext.py`](helpers_ext.py) — builders for the
  `test_content*.py` and `test_ext*.py` suites.
- [`helpers_audio.py`](helpers_audio.py) — PCM maths, spectral flatness (numpy and stdlib twins),
  word overlap, `zmqSTTRequest` framing; numpy is optional here on purpose.
- [`helpers_compose.py`](helpers_compose.py) — parity helpers for the two compose files.
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
| `test_runtime_turn`, `test_streaming`, `test_segment`, `test_brain_*`, `test_brains`, `test_backoff`, `test_connect_readiness`, `test_connection_resilience`, `test_clean_shutdown` | The supervisor turn loop, streaming chunks, fillers, reconnects |
| `test_action_*`, `test_actions_reach_the_robot`, `test_e2e_actions_to_robot`, `test_webhook_actions`, `test_launch_*` | `response_actions` from brain to robot; launch cards and sheet |
| `test_content*`, `test_render_*`, `test_automarkup`, `test_performance`, `test_faces` | Content packs, template sandbox and parity, markup floor, behavior planner |
| `test_ext*.py` | Sandboxed extensions: escapes X1–X12, conformance T1–T18, `act`/`subscribe` |
| `test_memory*`, `test_telemetry*`, `test_store*`, `test_transcript_memory_policy`, `test_soak_accounting` | Durable store, roll-up repair, erase and logging policy |
| `test_schedule*` | Day planner, parent requests, the "why" view, SIL end to end |
| `test_telehealth*`, `test_presence*`, `test_fleet*`, `test_cloud_config`, `test_device_permits`, `test_roster`, `test_why_no_config` | Config push, fleet defaults, pairing gate, telehealth |
| `test_console_*`, `test_parent_api`, `test_brain_console` | Parent console ⇄ supervisor contract (need `fastapi` + `httpx`) |
| `test_tts`, `test_stt*`, `test_voice_*`, `test_sim_tts_playback`, `test_speech_guard` | Voice engines, gateway STT, the tone-vs-speech guard |
| `test_config_*`, `test_assemble`, `test_dotenv_cannot_perturb_the_suite`, `test_env_hygiene_live_suites`, `test_no_deployment_defaults` | Config precedence and the dotenv fence |
| `test_compose`, `test_broker_acl`, `test_package_contents`, `test_render_container_deps` | Compose parity, broker ACL, what the wheel ships |
| `test_ci_*`, `test_clock_dependence`, `test_mutation_tables`, `test_readiness_guards_are_checked`, `test_harness_readiness`, `test_node_global_stubs`, `test_page_teeth_slow_mode`, `test_promotion_guard` | Guards on CI itself: workflows mirror `sim/ci/`, every `sim/test_*.mjs` is run by a tier, reviewed wall-clock reads |
| `test_csp_hashes`, `test_no_offsite_images`, `test_shared_ceilings`, `test_sim_client_parity`, `test_safety`, `test_sdk` | Static-site CSP, images, shared rate-limit tier, SDK and safety floor |
| `test_live_*.py`, `test_smoke_live_brain` | Real gateway completions, TTS, STT, hosted ears, voice round trip; skip without credentials |

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
