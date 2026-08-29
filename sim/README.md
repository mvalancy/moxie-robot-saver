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
| [`web/`](web/) | The WebGL 3D Moxie (three.js, vendored) + `bridge.js` (MQTT→avatar) — the UI. |
| [`virtual_moxie.py`](virtual_moxie.py) | The SIL robot: speaks the real MQTT protocol. `--scenario`/`--loop-seconds` replay conversations. |
| [`broker/ci-mosquitto.conf`](broker/) | Mosquitto with `:1883` (MQTT) + `:9001` (WebSocket for the browser). |
| [`scenarios/`](scenarios/) | Scripted conversations (JSON) for the demo + tests. |
| `run_smoke.sh` / `run_scenarios.sh` / `test_bridge.mjs` / `test_voice.mjs` / `test_qr.mjs` / `test_cloud.mjs` | The six test layers, all in the CI workflow ([`ci/ci.yml`](ci/) — a template; install to `.github/workflows/` to run on GitHub). `test_voice` exercises the real TTS/STT services and skips cleanly if they aren't running; `test_qr` asserts the browser QR encoder is byte-identical to the python toolkit; `test_cloud` asserts the cloud-console fixture keeps the real REST/MQTT shapes. |

## Without Docker
```sh
bash sim/run_smoke.sh          # broker + supervisor + one round-trip (needs mosquitto or docker)
cd sim/web && python3 -m http.server 8080   # serve the UI, then run a broker+supervisor separately
```

## What's real vs simulated
The firmware is the **contract, not the runtime** — we don't boot the RK3288 Android image (it needs
absent vendor HALs). The virtual robot speaks the real MQTT topics + JSON/markup, so "works in the sim"
means "works on a real re-homed robot." Scope + honest limits: [`sil-and-cicd.md`](../docs/architecture/sil-and-cicd.md#what-is-and-isnt-simulated--honest-scope).

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
