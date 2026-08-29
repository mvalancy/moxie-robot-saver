# 🕹️ `sim/` — Moxie software-in-the-loop simulator

Run and watch a **virtual Moxie** — the WebGL 3D robot (face, arms, head, body) driven by the **exact
protocol reverse-engineered from firmware v3.6.4-Zephyr / OTA v24.10.803** — with **no hardware**. See
[`docs/architecture/sil-and-cicd.md`](../docs/architecture/sil-and-cicd.md) for the design.

## One command

```sh
docker compose -f sim/docker-compose.yml up            # broker + supervisor + web UI
#            add: --profile demo                        # + a virtual robot that chats on a loop
```

Then open **http://localhost:8080** and click **Connect** (the UI talks MQTT-over-WebSocket to the
broker on `:9001`). Drive Moxie by hand with the panel, or use `--profile demo` to watch a scripted
conversation play out — the 3D Moxie speaks, emotes, gestures, and shows event icons from the live bus.

## Pieces
| Path | What |
|---|---|
| [`web/`](web/) | The WebGL 3D Moxie (three.js, vendored) + `bridge.js` (MQTT→avatar) — the UI. |
| [`virtual_moxie.py`](virtual_moxie.py) | The SIL robot: speaks the real MQTT protocol. `--scenario`/`--loop-seconds` replay conversations. |
| [`broker/ci-mosquitto.conf`](broker/) | Mosquitto with `:1883` (MQTT) + `:9001` (WebSocket for the browser). |
| [`scenarios/`](scenarios/) | Scripted conversations (JSON) for the demo + tests. |
| `run_smoke.sh` / `run_scenarios.sh` / `test_bridge.mjs` | The three test layers (also in CI, [`ci/ci.yml`](ci/)). |

## Without Docker
```sh
bash sim/run_smoke.sh          # broker + supervisor + one round-trip (needs mosquitto or docker)
cd sim/web && python3 -m http.server 8080   # serve the UI, then run a broker+supervisor separately
```

## What's real vs simulated
The firmware is the **contract, not the runtime** — we don't boot the RK3288 Android image (it needs
absent vendor HALs). The virtual robot speaks the real MQTT topics + JSON/markup, so "works in the sim"
means "works on a real re-homed robot." Scope + honest limits: [`sil-and-cicd.md`](../docs/architecture/sil-and-cicd.md#what-is-and-isnt-simulated--honest-scope).
