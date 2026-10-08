# sim — the Moxie simulator

A virtual Moxie you can watch in a browser: a WebGL 3D robot (face, arms, head, body) driven by
the real MQTT protocol recovered from firmware v24.10.803. No hardware needed. The same folder
holds the static website (`web/`), the virtual robot used by the tests, and most of the test
suites. Design and scope: [`docs/architecture/sil-and-cicd.md`](../docs/architecture/sil-and-cicd.md).

## Run it

```sh
docker compose -f sim/docker-compose.yml up                          # broker + supervisor + web UI
docker compose -f sim/docker-compose.yml --profile voice up          # + Piper voice (:8081) and Whisper ears (:8082)
docker compose -f sim/docker-compose.yml --profile demo up           # + a virtual robot chatting on a loop
```

Open <http://localhost:8080> (the hub) or <http://localhost:8080/sim> (the simulator) and
click **Connect**. The browser talks MQTT over WebSocket to the broker on `:9001`. Drive Moxie
from the panel, press **Demo** to replay a recorded session, or use the `demo` profile.

The supervisor starts with the echo brain. For a real LLM, set `MOXIE_APP=llm` and the
`MOXIE_LLM_*` settings in `mqtt/.env` ([`mqtt/`](../mqtt/README.md)).

Without Docker:

```sh
bash sim/run_smoke.sh          # broker + supervisor + one round trip (needs mosquitto or docker)
python3 sim/serve.py           # serve sim/web on 127.0.0.1:8080 with cache-busting
```

## What's in here

| Path | What |
|---|---|
| [`web/`](web/README.md) | The static site: simulator (`sim.html`), hub (`index.html`), setup page, example parent console, docs explorer. Deployed to Cloudflare Pages. |
| [`virtual_moxie.py`](virtual_moxie.py) | The virtual robot: speaks the real MQTT protocol; `--scenario` and `--loop-seconds` replay conversations. |
| [`broker/`](broker/README.md) | Mosquitto config with MQTT `:1883` and WebSocket `:9001`. |
| [`scenarios/`](scenarios/README.md) | Scripted conversations (JSON) for the demo and tests. |
| [`tts/`](tts/README.md), [`stt/`](stt/README.md) | Local Piper voice and faster-whisper ears services. |
| [`tools/`](tools/README.md) | Build scripts (docs bundle, CSP hashes, pre-rendered audio), probes and mutation checkers. |
| [`tests/`](tests/README.md) | The pytest suites. |
| [`ci/`](ci/README.md) | GitHub Actions workflow templates. |
| `run_smoke.sh` | Broker, supervisor and one round trip. `--telehealth` for the puppet path; `MOXIE_SIL_PORT` / `MOXIE_STATUS_PORT` pick free ports. |
| `run_scenarios.sh` | Replays every scenario through a live stack. |
| `run_soak.sh` | Fault-injection soak: `--profile smoke\|quick\|week` (about 1, 5 or 60 minutes). |
| `run_compose_smoke.sh` | Brings up the root `docker-compose.yml` on spare ports and round-trips the virtual robot through it. |
| `nginx.conf` | The `web` service's server block in `docker-compose.yml`: serves `/sim` as `sim.html`, as Cloudflare Pages and `serve.py` do (the hub links `/sim`). |
| `run_acl_proof.sh` | Proves the broker ACL against a real mosquitto. |
| `run_broker_outage.sh` | Stops and restarts a real broker under a running supervisor. |
| `readiness.sh` | The shared "wait for the stack" helpers: a TCP connect to the broker, then the supervisor's `subscriptions acknowledged by the broker` log line. Never a fixed sleep. |
| `test_*.mjs` | Node suites: the bridge, voice, QR, audio, edge Functions, docs explorer, and headless-browser suites (layout, CSP, microphone spend, background tab). |
| `bridge_harness.mjs`, `browser_harness.mjs` | Shared plumbing for the node suites (not tests themselves). |
| `check_deployed.mjs` | Checks a deployed site in a phone-sized browser. Spends nothing. See [`ci/`](ci/README.md). |
| `check_hosted_mic.mjs` | Plays a voice into Chrome's fake microphone against a deployment. `--dry-run` is free; a real run spends gateway calls. |
| `check_live_turn.mjs` | The daily canary: ONE real chat turn against a deployment (spends one completion; the voice ticket is never redeemed). `--selftest` is hermetic. |
| `eval_live.mjs` | Scores real multi-turn conversations against a deployment. Spends money; refuses to run without `--yes`. Not a test. |

## What is real and what is simulated

We do not boot the robot's Android image (it needs vendor hardware drivers). The virtual robot
speaks the real MQTT topics, JSON and markup, so "works in the sim" means the backend behaves
correctly toward a re-homed robot. It does not prove what the robot's own face and body do with
that output. See [`sil-and-cicd.md`](../docs/architecture/sil-and-cicd.md).

## Voice (Piper)

```sh
python3 -m venv /tmp/piper-venv && /tmp/piper-venv/bin/pip install piper-tts
python3 sim/ci/fetch_piper_voices.py      # pinned, checksummed voices into sim/tts/voices/
python3 sim/tts/server.py 8081            # GET /tts?text=... -> audio/wav, GET /health
```

The simulator's Audio panel points at port 8081 on the page's host by default. `MOXIE_PIPER_VOICE`
picks a specific `.onnx` file. Voice files are git-ignored (63 MB each).

## Ears (faster-whisper)

```sh
/tmp/piper-venv/bin/pip install faster-whisper
python3 sim/stt/server.py 8082            # POST /stt (audio) -> DeepgramResponse, GET /health
```

Click **Listen**, talk, and click again. The transcript goes to the brain as a child utterance on
`/devices/<id>/events/remote-chat`, and Moxie speaks the reply. `MOXIE_STT_MODEL` picks the model
(default `base.en`).
