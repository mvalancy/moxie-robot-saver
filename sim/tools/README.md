# 🛠️ `sim/tools/` — build helpers, probes and mutation checkers

Hand-run and CI-run tools for the static site, the SIL stack and the test suites themselves. Run everything from the repo root.

## Build helpers (their output is committed)

- [`build_docs_bundle.py`](build_docs_bundle.py) — copies `docs/` (+ top-level `README`/`ROADMAP`) into `sim/web/docs-bundle/` and writes `docs-index.json` / `docs-search.json` for the docs explorer.
- [`check_bundle_fresh.py`](check_bundle_fresh.py) — exit 1 if the committed docs bundle differs from a fresh rebuild.
- [`build_csp_hashes.py`](build_csp_hashes.py) — regenerates the `script-src` SHA-256 hashes in `sim/web/_headers` from the pages' inline `<script>` blocks; `--check` verifies. Refuses inline `on*=` handlers and `javascript:` URLs.
- [`prerender_audio.py`](prerender_audio.py) — renders scripted lines to `sim/web/audio/{moxie,child,ambient}/<hash>.mp3` + `index.json` for the static demo, with local Piper (`--engine piper`, the default) or any OpenAI-compatible `/audio/speech` (`--engine gateway`: base URL, key and model from flags, the environment or `--env-file mqtt/.env`; no host is hard-coded). The shipped Moxie clips are all `tts-piper-kristin` through the gateway; `--rerender moxie --rerender ambient` re-renders every one, skipping clips already in that voice, so a stopped run resumes. `--max-calls N` caps gateway spend, retries included. Tested by [`../tests/test_prerender_gateway.py`](../tests/test_prerender_gateway.py) against a loopback stub; see [`../web/audio/README.md`](../web/audio/README.md).
- [`build_ext_conformance.py`](build_ext_conformance.py) — regenerates `sim/tests/data/ext_conformance.json`, the six hand-ported OpenMoxie hooks.
- [`build_performance_goldens.py`](build_performance_goldens.py) — regenerates `sim/tests/goldens/performance.json`, the behavior planner's 22 dialog-act goldens.

## Probes and harnesses

- [`check_promotion_state.py`](check_promotion_state.py) — was the last `dev → main` squash reconciled back into `dev`? Exit 0 yes (or within the 30-minute grace), 1 no, 2 could not measure. Run hourly by [`../ci/promotion.yml`](../ci/README.md).
- [`soak.py`](soak.py) — the SIL soak behind [`../run_soak.sh`](../run_soak.sh): real mosquitto (container), real `mqtt/run.py`, virtual robots, `MOXIE_APP=echo`; profiles `smoke` / `quick` / `week`, numeric bars printed pass or fail.
- [`first_audio_ab.py`](first_audio_ab.py) — first-audio latency A/B across `MOXIE_EXPRESSIVE` on the real stack; `--brain stub` (free) or `--brain live` (one completion per turn).
- [`measure_voice_latency.mjs`](measure_voice_latency.mjs) — send-to-first-audio of typed turns in real Chrome against a real deployment of the hosted Sim (a local `wrangler pages dev` or the site): per turn the chat round trip, each voice chunk's round trip, the gaps between chunks and the reply length, then the medians. Spends one chat and up to three speech calls a turn; `--yes --base=URL [--turns=10] [--out=FILE] [--label=arm]`.
- [`prove_broker_acl.py`](prove_broker_acl.py) — delivery-based assertions that a real mosquitto enforces the P0 ACL; driven by [`../run_acl_proof.sh`](../run_acl_proof.sh).
- [`assert_no_secret_in_log.py`](assert_no_secret_in_log.py) — fails if a log contains a gateway credential, without ever printing it; used by `sim/run_smoke.sh --live-brain`.
- [`probe_demo_gateway.mjs`](probe_demo_gateway.mjs) — POSTs the bodies the Pages Functions build to the real gateway and reports response shapes (hand-run, reads `mqtt/.env` or `MOXIE_ENV_FILE`; `--dry-run`, `--only=`).
- [`grounding_probe.mjs`](grounding_probe.mjs) / [`grounding_score.mjs`](grounding_score.mjs) — paid A/B of the model's answer with and without the retrieved passage, and its pure scoring seam. Requires `--yes --max-attempts 4..6 --timeout-ms 1000..60000`.
- [`probe_budget.mjs`](probe_budget.mjs) — shared outbound-fetch counter and deadline for opt-in live probes; guarded in [`../test_mode.mjs`](../test_mode.mjs).
- [`run_live_action_tags.sh`](run_live_action_tags.sh) → [`run_live_action_tags.py`](run_live_action_tags.py) + [`action_tag_campaign.py`](action_tag_campaign.py) — the one bounded live goodbye-tag campaign: six-attempt ceiling, total deadline (`MOXIE_CAMPAIGN_TIMEOUT_SECONDS`, default 360), prints only allow-listed counts.
- [`page_teeth_check.py`](page_teeth_check.py) + [`teeth_ledger.mjs`](teeth_ledger.mjs) + [`teeth_hook.mjs`](teeth_hook.mjs) — serves each browser suite a deliberately broken site (script deleted, inert, 404'd, stalled) and reports which checks stay green. Not in CI.

## Mutation checkers

Each removes one guard at a time and requires the named test to go red; a green suite shows a guard is present, this shows it is load-bearing. All share one runner, [`mutation_runner.py`](mutation_runner.py): it refuses a red baseline, an anchor that is missing or ambiguous, and a `-k` selector that matches no test, and prints `N/N caught`. Pass row names (`X1 D4`) to run only those rows. None run in CI; `sim/tests/test_mutation_tables.py` (fast tier) checks every anchor matches exactly once and no mutation is committed.

```sh
python3 sim/tools/authoring_mutation_check.py
python3 sim/tools/brain_mutation_check.py
python3 sim/tools/ears_mutation_check.py
python3 sim/tools/ext_mutation_check.py
python3 sim/tools/hardening_mutation_check.py
python3 sim/tools/hardening_p1_mutation_check.py
python3 sim/tools/launch_card_mutation_check.py
python3 sim/tools/performance_mutation_check.py
python3 sim/tools/subscribe_mutation_check.py
python3 sim/tools/telemetry_rollup_mutation_check.py
python3 sim/tools/turnstile_mutation_check.py
python3 sim/tools/unit_budget_mutation_check.py
```

| Table | Guards the… | Run after touching |
|---|---|---|
| `authoring` | content editor | authoring region of `moxie_runtime/`, `packs.shadow_check`, `render.render_prompt` |
| `brain` | brain registry | `moxie_sdk/brains.py` and its runtime |
| `ears` | honest ears: no-call floor, label strip, phantom drop, local VAD, kill switch | `moxie_sdk/stt.py` (`SttSession`, `WhisperTranscriber`), `moxie_runtime/voice.py` `feed_stt` |
| `ext` | extension sandbox | `ext/`, `render.py`, `ext_host.py`, `packs/` pattern cap |
| `hardening` | P0: store lock, connection region | `moxie_sdk/store.py`, `moxie_runtime/` connection code |
| `hardening_p1` | P1: roster, conn telemetry, shutdown | `moxie_sdk/{roster,conn_telemetry}.py`, `fleet/activity.py` |
| `launch_card` | launch-card QR allowlist | `moxie_sdk/launch_cards.py` |
| `performance` | behavior planner | `moxie_sdk/performance.py` |
| `subscribe` | `subscribe` capability | `moxie_runtime/` subscription merge, `ext.SUBSCRIBE_EVENTS` |
| `telemetry_rollup` | ring vs. daily roll-up | `moxie_sdk/telemetry.py`, `moxie_runtime/telemetry.py` |
| `turnstile` | Turnstile bot control | `functions/api/_lib/turnstile.js`, `chat.js`, `transcribe.js`, `sim/web/turnstile.js` |
| `unit_budget` | shared per-colo / per-IP ceilings | `functions/api/_lib/{limits,counters,sharedtier}.js` |

## Gotchas

- `turnstile` and `unit_budget` never touch the checkout: they mutate a throwaway hardlink copy, and a row counts as caught only if its selector appears in a failing check's own label. Pass row names (`U3 D4`) to re-check a few rows in seconds.
- An anchor must match exactly once: `str.replace(old, new, 1)` would otherwise mutate whichever copy comes first and still print "caught". The runners refuse a non-unique anchor, and `test_mutation_tables.py` enforces it for every table.
- The pytest-based tables run the repo's `.venv/bin/python`; create the venv first.
- `ext` and the pytest-based tables revert in a `finally` and set `PYTHONDONTWRITEBYTECODE`; a killed run can leave the tree mutated, so check `git status` afterwards.
- `page_teeth_check.py --selftest` takes about a minute; the full sweep (`--baseline-dir DIR`) takes hours and mutates `sim/web` transiently. `--check-tree --restore` undoes a breakage an interrupted run left behind.
- The docs bundle is generated and committed; it is laid out one doc per line with no global hash so branches merge cleanly. `check_bundle_fresh.py` is the only authority on freshness.
- `prerender_audio.py` keys `index.json` by the exact utterance string; punctuation must match `stub.js` / `ambient.json` / `filler.py` (guarded by `sim/test_fallback_coverage.mjs`).
- `prerender_audio.py` records each clip's voice in an ID3 `TXXX:moxie_voice` frame, and `test_prerender_gateway.py` fails if any Moxie clip is not in the shipped voice. A new line rendered with the default `--engine piper` therefore fails it: render it with the command in [`../web/audio/README.md`](../web/audio/README.md). The gateway engine never renders child lines (another speaker), never prints the key, and never follows a redirect with it.

---
📖 [sim](../README.md) · [Back to top](../../README.md)
