# 🕹️ `sim/` — Moxie software-in-the-loop simulator

Run and watch a **virtual Moxie** — the WebGL 3D robot (face, arms, head, body) driven by the **exact
protocol reverse-engineered from firmware v3.6.4-Zephyr / OTA v24.10.803** — with **no hardware**. See
[`docs/architecture/sil-and-cicd.md`](../docs/architecture/sil-and-cicd.md) for the design.

## One command

```sh
docker compose -f sim/docker-compose.yml up                   # broker + supervisor + web UI
#                                    --profile voice          # + Piper TTS + whisper STT (Moxie speaks & listens)
#                                    --profile demo           # + a virtual robot that chats on a loop
#                        --profile voice --profile demo       # everything
```
> The `voice` profile needs a Piper voice on disk first — see [Voice (Piper TTS)](#voice-piper-tts).

Then open **http://localhost:8080** (the hub; the simulator is at **/sim.html**) and click **Connect** (the UI talks MQTT-over-WebSocket to the
broker on `:9001`). Drive Moxie by hand with the panel, hit **Play demo** to replay a canned birthday session (no broker
needed), or use `--profile demo` to watch a scripted conversation play out — the 3D Moxie speaks, emotes, gestures, and shows event icons from the live bus.

## Pieces
| Path | What |
|---|---|
| [`web/`](web/) | The WebGL 3D Moxie (three.js, vendored) + `bridge.js` (MQTT→avatar) + `audio.js` (plays the server's `CloudTTSResponse`) — the UI. |
| [`virtual_moxie.py`](virtual_moxie.py) | The SIL robot: speaks the real MQTT protocol. `--scenario`/`--loop-seconds` replay conversations. |
| [`broker/ci-mosquitto.conf`](broker/) | Mosquitto with `:1883` (MQTT) + `:9001` (WebSocket for the browser). |
| [`scenarios/`](scenarios/) | Scripted conversations (JSON) for the demo + tests. |
| [`run_compose_smoke.sh`](run_compose_smoke.sh) + [`compose-smoke.env`](compose-smoke.env) | Proof for the **[one-command stack](../docs/guides/one-command-stack.md)**: brings the repo-root `docker-compose.yml` up under a throwaway project on unused ports, round-trips the virtual robot (incl. TTS audio) through it, checks the console's `/local/fleet`, tears it down. |
| [`run_acl_proof.sh`](run_acl_proof.sh) + [`tools/prove_broker_acl.py`](tools/) | Proof for **[broker hardening P0](../docs/architecture/backlog/security-broker-auth.md)**: starts a throwaway mosquitto from `mqtt/broker/{compose-mosquitto.conf,acl,acl-robot}` with a scratch credential, then asserts **by message delivery** (MQTT 3.1.1 acks an authorization failure as success) that the supervisor authenticates, that a robot cannot read another robot's config or `$SYS/broker/log`, and that the browser SIM keeps its observer view. |
| [`run_broker_outage.sh`](run_broker_outage.sh) | Proof for **[production hardening P0](../docs/architecture/backlog/production-hardening.md)** §4.1: takes a **real broker away from a running supervisor** and gives it back. Five phases against a mosquitto container it owns — a cold start with no broker at all (it must wait and retry, not die), the broker appearing, a SIL turn, the outage (`/status` flips `broker_connected`, stamps `last_broker_disconnect`, counts the gap's publish in `publish_drops`, and `POST /wakeup` answers **409 with a reason** instead of `published: true`), and the reconnect with the next turn end to end. The unit tests for all of that use fakes; this is the only thing that stops a real socket. |
| `run_smoke.sh` / `run_scenarios.sh` / `test_bridge.mjs` / `test_automarkup_render.mjs` / `test_voice.mjs` / `test_qr.mjs` / `test_cloud.mjs` / `test_audio.mjs` | The eight test layers, all in the CI workflow ([`ci/ci.yml`](ci/) — a template; install to `.github/workflows/` to run on GitHub). `test_voice` exercises the real TTS/STT services and skips cleanly if they aren't running; `test_qr` asserts the browser QR encoder is byte-identical to the python toolkit; `test_cloud` asserts the cloud-console fixture keeps the real REST/MQTT shapes; `test_audio` asserts the browser decodes and plays a `CloudTTSResponse` (PCM maths, chunk order, lip-sync) and round-trips that decoder against the real server encoder; `test_automarkup_render` drives the eight byte-exact markup-floor goldens ([`sim/tests/goldens/annotate.json`](tests/goldens/annotate.json)) through the real `bridge.js` and asserts the avatar reaches six distinct faces and moves its arms — the SIM is the **only renderer we can assert against**, since no hardware has ever played our markup. |
| [`browser_harness.mjs`](browser_harness.mjs) + `test_typed_turn.mjs` / `test_mic_spend.mjs` / `test_mobile_layout.mjs` / `test_csp.mjs` / `test_bg_perf.mjs` | The **headless-browser** layer, added 2026-09-03 after three defects that no fake-DOM suite could have seen. `browser_harness.mjs` is shared plumbing (puppeteer/Chrome discovery, a static server that can send the real `web/_headers`, a real PCM tone fixture) and is not itself a test. `test_typed_turn` drives a typed line to `/api/chat` and asserts the **peak sample amplitude** of the buffer handed to Web Audio — a silent clip fails it — plus that the controls which cannot work on a hosted origin are disabled and produce no CSP error. `test_mobile_layout` hit-tests the bottom-anchored controls with `document.elementFromPoint()` at four phone widths (the hosted banner used to sit on top of `#rail-toggle`; it was visible the whole time, so only a hit test could catch it). `test_mic_spend` counts the requests that actually leave the page when a microphone press fails, and pairs every "spends nothing" with a Web Audio assertion that the visitor was still consoled *out loud* — a scripted consolation line used to travel `sendUserTurn` and buy a real chat + speech turn on words nobody said. `test_csp` serves every page with the shipped security headers **actually applied** — the first suite here that does, and the only thing that can tell a safe policy from one that blanks a page. `test_bg_perf` opens a **second** page and `bringToFront()`s it, which is the only way a headless run gets a genuinely hidden tab (`document.hidden` stays false in a lone page, and a measurement that missed that talked itself into a reproduction it had not got) — then asserts that `bg.js` accumulates **nothing** while rAF is paused, with a teeth block that rebuilds the old `setInterval` producer shape out of the shipped file and requires the growth to reappear, so an environment that cannot background a tab skips green instead of passing on nothing. |

## Without Docker
```sh
bash sim/run_smoke.sh          # broker + supervisor + one round-trip (needs mosquitto or docker)
                               #   --telehealth   the puppet round-trip instead
                               #   MOXIE_SIL_PORT / MOXIE_STATUS_PORT pick free ports
bash sim/run_soak.sh           # the SIL soak: fault injection + 12 numeric bars (needs docker)
                               #   --profile smoke|quick|week   (~1 min / ~5 min / 60 min)
                               #   --only-contention            (the store half; no broker needed)
bash sim/run_acl_proof.sh      # the broker ACL, against a real mosquitto (needs docker)
bash sim/run_broker_outage.sh  # stop and start a real broker under a live supervisor (needs docker)
cd sim/web && python3 -m http.server 8080   # serve the UI, then run a broker+supervisor separately
```

The boot of every script above is **waited on, never slept through**: [`readiness.sh`](readiness.sh) holds the two waits (a TCP connect to the broker, the supervisor's own `[runtime] broker connected` line) that `run_smoke.sh` and `run_scenarios.sh` both source. A fixed `sleep` is wrong in both directions — it wastes seconds on a warm box and, on a loaded one, turns a boot that had not finished into a timeout blamed on the robot.

## What's real vs simulated
The firmware is the **contract, not the runtime** — we don't boot the RK3288 Android image (it needs
absent vendor HALs). The virtual robot speaks the real MQTT topics + JSON/markup, so "works in the sim"
means "works on a real re-homed robot." Scope + honest limits: [`sil-and-cicd.md`](../docs/architecture/sil-and-cicd.md#what-is-and-isnt-simulated-honest-scope).

## Voice (Piper TTS)

Moxie speaks via a local **Piper** service — offline, no cloud, no API key:

```sh
# 1) a python with piper installed
python3 -m venv /tmp/piper-venv && /tmp/piper-venv/bin/pip install piper-tts
# 2) a voice (amy is the default preference)
curl -L -o sim/tts/voices/en_US-amy-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx
curl -L -o sim/tts/voices/en_US-amy-medium.onnx.json \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json
# 3) run it
python3 sim/tts/server.py 8081     # GET /tts?text=... -> audio/wav, /health
```

The web UI's **Audio** panel points at `http://127.0.0.1:8081` by default (editable).
Voice `.onnx` files are gitignored (63 MB each).

## Ears (STT — talk to Moxie)

The browser mic feeds a local **faster-whisper** service that returns the robot's own
**Deepgram-compatible** shape (`DeepgramResponse`), so the same service serves the sim
and a real robot:

```sh
/tmp/piper-venv/bin/pip install faster-whisper     # or any python env
python3 sim/stt/server.py 8082                     # POST /stt (audio) -> DeepgramResponse, GET /health
```

Then click **Listen** in the VOICE group, talk, click again to stop — the transcript is
published as a child utterance on `/devices/<id>/events/remote-chat`, the brain answers,
and Moxie speaks the reply (Piper). Model via `MOXIE_STT_MODEL` (default `base.en`).
