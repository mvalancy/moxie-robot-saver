# sim/tests — the Python test suite

About 140 pytest files covering the SDK, the supervisor, the parent console, the hosted Functions'
shared limits, the CI setup itself, and a Playwright suite that drives the static site in a real
browser. Most are hermetic: no broker, no browser, no network, no keys.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -q -r sim/tests/requirements.txt

# hermetic suite (what CI runs on every push); blank the key so nothing spends money
MOXIE_LLM_API_KEY= .venv/bin/python -m pytest sim/tests -q -k "not test_sil and not test_live"

sim/tests/run.sh              # everything, including the browser suite (sets up its own venv)
sim/tests/run.sh -q -k alive  # any pytest args pass through
```

`run.sh` reuses a locally cached Chrome (`~/.cache/puppeteer/...`). With no Chrome available the
browser tests skip cleanly.

## Requirements files

| File | Contents |
|---|---|
| `requirements-hermetic.txt` | The one declaration of what the suite needs. It pulls in `mqtt/requirements.txt` and `server/requirements.txt`. Every CI job that runs pytest installs it. |
| `requirements.txt` | The hermetic list plus `playwright`, nothing else. Used by `run.sh` and local venvs. |

Never hand-list packages in a workflow or an agent brief; `test_ci_workflows.py` enforces this.

## Which tier runs what

| Tier | What runs |
|---|---|
| Fast CI (`dev`) | The whole hermetic suite, then `test_sil.py` and the other browser tests in a separate job. |
| Deep CI (`main`) | The hermetic suite again, plus SIL smoke, scenarios, broker outage, compose stack and soak. |
| Deep CI, manual dispatch | The `test_live_*` suites against a real gateway (spends real calls). |
| Local only | Anything you run with a key present — keep `not test_live` unless you mean it. |

Details: [`../ci/README.md`](../ci/README.md).

## Map of the suites

| Area | Files |
|---|---|
| **Turn pipeline** | `test_runtime_turn`, `test_streaming`, `test_segment`, `test_brain_latency`, `test_backoff`, `test_safety`, `test_why_no_config` |
| **Brains** | `test_brains` (registry), `test_brain_runtime` (live swap), `test_brain_console`, `test_config_brain_endpoint`, `test_sdk`, `test_webhook_actions` |
| **Actions and markup** | `test_action_tags`, `test_action_tag_campaign`, `test_actions_reach_the_robot`, `test_e2e_actions_to_robot`, `test_automarkup`, `test_performance` |
| **Content** | `test_content`, `test_content_app`, `test_content_wiring`, `test_content_packs`, `test_content_packs_runtime`, `test_content_pack_sandbox`, `test_content_authoring`, `test_render_sandbox`, `test_render_sandbox_parity`, `test_render_fallback`, `test_render_container_deps` |
| **Sandboxed extensions** | `test_ext` (T1–T18), `test_ext_escapes` (X1–X12), `test_ext_act`, `test_ext_subscribe`; conformance data in [`data/`](data/README.md) |
| **Memory and schedules** | `test_memory`, `test_memory_runtime`, `test_memory_view`, `test_schedule`, `test_schedule_planner`, `test_schedule_view`, `test_launch_cards`, `test_launch_cards_runtime`, `test_launch_sheet` |
| **Speech** | `test_stt`, `test_stt_gateway`, `test_tts`, `test_voice_settings`, `test_voice_runtime`, `test_speech_guard`, `test_sim_tts_playback` |
| **Config, telemetry, privacy** | `test_cloud_config`, `test_fleet_config`, `test_telemetry`, `test_telemetry_runtime`, `test_telemetry_rollup_repair`, `test_telemetry_erase_policy`, `test_transcript_memory_policy`, `test_conn_telemetry`, `test_faces`, `test_presence`, `test_presence_runtime` |
| **Store and robots** | `test_store`, `test_store_concurrency`, `test_roster`, `test_device_permits`, `test_broker_acl`, `test_telehealth`, `test_telehealth_runtime`, `test_telehealth_view` |
| **Supervisor lifecycle** | `test_connection_resilience`, `test_connect_readiness`, `test_clean_shutdown`, `test_harness_readiness`, `test_readiness_guards_are_checked`, `test_soak_accounting` |
| **Parent console and REST** | `test_parent_api`, `test_fleet`, `test_console_roundtrip`, `test_console_content`, `test_console_devices`, `test_console_memory`, `test_console_telehealth`, `test_console_voice` |
| **Stack and packaging** | `test_assemble`, `test_compose`, `test_package_contents`, `test_config_dotenv`, `test_config_dotenv_comments`, `test_dotenv_cannot_perturb_the_suite` |
| **Hosted site** | `test_csp_hashes`, `test_shared_ceilings`, `test_no_deployment_defaults`, `test_no_offsite_images`, `test_sim_client_parity` |
| **Browser and SIL** (need a broker or Chrome) | `test_sil` (every page, resolution and control), `test_sil_*`, `test_launch_cards_sil`, `test_presence_sil`, `test_schedule_sil_e2e`, `test_smoke_live_brain` |
| **Live** (need a key; spend real calls) | `test_live_gateway`, `test_live_gateway_stt`, `test_live_gateway_tts`, `test_live_gateway_turn_e2e`, `test_live_action_tags`, `test_live_content_e2e`, `test_live_talk_e2e`, `test_live_hosted_ears`, `test_live_telehealth_voice`, `test_live_voice_picker`, `test_env_hygiene_live_suites` |
| **Guards on the test setup itself** | `test_ci_workflows`, `test_ci_test_coverage` (every test file is run by some tier), `test_ci_browser_suites_actually_run`, `test_clock_dependence`, `test_mutation_tables`, `test_node_global_stubs`, `test_page_teeth_slow_mode`, `test_promotion_guard` |

Shared helpers: `helpers_runtime.py` (a fake MQTT client and `drive_turn`; use it instead of writing
your own), `helpers_content.py`, `helpers_ext.py`, `helpers_console*.py`, `helpers_compose.py`,
`helpers_stack.py`, `helpers_audio.py`, `helpers_web.py`, `helpers_qr_matrix.py`.

Subfolders:

- [`edge/`](edge/README.md) — sections of the Pages Functions node suites (`sim/test_demo_proxy.mjs`
  and friends). They are `.mjs`, so pytest never collects them.
- [`hosted_mic/`](hosted_mic/README.md) — the scorer and browser probe behind
  `sim/check_hosted_mic.mjs`.
- [`goldens/`](goldens/README.md) — recorded expected outputs.
- [`data/`](data/README.md) — test data.

## Rules that keep the suite green

- **Browser tests assert what the page recorded, never a live sample.** The page keeps records such
  as `moxieAudio.lastMouthPeak()` and `lastPlaybackStats()`; tests wait for the utterance to finish,
  then assert the record. Sampling a moving value flakes on a loaded runner.
- **A test that uses a fake brain must not need the real SDK.** `LLMApp` and the voice synthesizer
  take a `client=` argument; `pytest.importorskip("openai")` is only for live tests.
- **Your `.env` cannot change the suite.** `conftest.py` sets `MOXIE_SKIP_DOTENV=1` before
  collection, so a local `mqtt/.env` cannot make "nothing configured" tests pass for the wrong
  reason. The live suites still find credentials through `helpers_runtime.load_repo_dotenv`, which
  loads only the keys they need (`LIVE_KEYS`), and a fixture hides those from hermetic tests.
  `test_dotenv_cannot_perturb_the_suite.py` guards both halves. To run against a deployment's
  settings on purpose: `MOXIE_SKIP_DOTENV=0 pytest sim/tests`, or `MOXIE_DOTENV=<file>` for a
  fixture.
