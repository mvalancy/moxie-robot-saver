# Moxie Robot Saver

**Bring a Moxie robot fully back to life — end to end — on your own hardware, with no cloud.**

Embodied Inc. shut down in December 2024, took its servers offline, and bricked every Moxie
robot in the field. This project is a complete, self-hosted replacement for **everything Moxie
needed the internet for** — so an owner can pair a robot, configure it, and (as the project
grows) have it *talk again* — all running on one machine in their home.

> **The goal:** unlock every Moxie so kids get their robot back, and keep a genuinely lovely
> piece of hardware out of the landfill. **Local-first, private, no account, no subscription.**

## The vision: one box, the whole Moxie backend

Picture a single machine on your home network — a **gaming PC, a home server, an NVIDIA Jetson
Orin, anything with a GPU** — running the entire Moxie cloud locally:

```
                         ┌───────────────────  YOUR MACHINE (no internet needed)  ───────────────────┐
                         │                                                                            │
 your phone ──LAN──►  Parent-app server  ──┐                                                          │
 (web app)            (REST + web UI)       │  issues Wi-Fi QR & endpoint QR, stores account/child    │
                         │                  │                                                         │
 your Moxie ──Wi-Fi──►  MQTT broker  ───────┼──►  Conversation engine  ──►  Local LLM  (any OpenAI-   │
 (robot)              (TLS 8883)            │      (STT ► LLM ► markup ►      compatible endpoint as   │
                         │                  │       TTS ► speak)              fallback)                │
                         │                  └──►  Local STT (Whisper)  +  Local TTS                    │
                         └────────────────────────────────────────────────────────────────────────────┘
```

Nothing leaves your home. Local models by default; an OpenAI-compatible endpoint is an *optional*
fallback, never a requirement.

## Two channels, three steps

Reviving a Moxie means replacing **two independent cloud connections**:

| Channel | Protocol | What it does | This repo |
|---------|----------|--------------|-----------|
| **Parent app → cloud** | REST/HTTPS | account, child profile, **pairing**, Wi-Fi, robot settings | ✅ **built** (`server/`) |
| **Robot → cloud** | MQTT/TLS | the live experience: **conversations**, activities, STT/LLM/TTS | 🔨 **in progress** (`mqtt/`, `ai/`) |

And the robot is revived by showing it a short sequence of QR codes (no teardown, no serial cable):

1. **Wi-Fi QR** — gets Moxie onto your network. ✅ *Built and verified on real hardware.*
2. **Endpoint QR** — tells Moxie to use *your* server instead of Embodied's dead cloud. 🔨 *Next.*
3. Moxie connects over MQTT and **talks**, powered by your local models. 🔨 *After that.*

See [`docs/architecture/revival-path.md`](docs/architecture/revival-path.md) for the full decision
tree (including the firmware requirement) and [`ROADMAP.md`](ROADMAP.md) for the phased plan.

## Status

**Phase 1 — parent app — is working**, and its Wi-Fi QR has been **verified against a real Moxie**
(the robot scanned our clean-room QR and joined the network). What runs today:

- A faithful, account-free reimplementation of the parent-app REST API (`client-service-api.embodied.com`).
- A mobile web app your phone opens over the LAN to set up a child, enter Wi-Fi, and generate the pairing QR.
- Clean-room pairing-QR tooling + deterministic recovery-key crypto, matched to the decompiled app.
- A hardware-free test path (`simulate-robot-scan`) that completes pairing with no robot.

**Phases 2–3 — the MQTT broker, conversation engine, and local AI — are being specced and built now.**

## Quick start (Phase 1)

```bash
git clone https://github.com/mvalancy/moxie-robot-saver.git
cd moxie-robot-saver
pip install -r server/requirements.txt
python server/run.py                 # serves on 0.0.0.0:8080
```

Open **`http://<this-computer-ip>:8080`** from your phone (same LAN, or Tailscale), enter Wi-Fi,
generate the QR, hold it to Moxie. Details: [`docs/guides/first-time-setup.md`](docs/guides/first-time-setup.md).

## Repository layout

| Path | What | Phase |
|------|------|-------|
| [`server/`](server/) | Parent-app server (REST API + mobile web client + crypto) | 1 ✅ |
| [`tools/pairing/`](tools/pairing/) | Clean-room QR codec + CLI | 1 ✅ |
| [`mqtt/`](mqtt/) | Robot cloud: MQTT broker, endpoint QR, conversation engine | 2–3 🔨 |
| [`ai/`](ai/) | Local LLM / STT / TTS adapters (OpenAI-compatible fallback) | 3 🔨 |
| [`hardware/`](hardware/) | The robot itself: OS, firmware, finding it on the LAN | ref |
| [`docs/`](docs/) | The complete map — see [`docs/README.md`](docs/README.md) | — |

## Documentation

Start at [`docs/README.md`](docs/README.md). Highlights:
- **Architecture** — [`overview`](docs/architecture/overview.md), [`revival path`](docs/architecture/revival-path.md)
- **Reverse-engineering** (source of truth) — [`REST API`](docs/reverse-engineering/rest-api.md), [`crypto & keys`](docs/reverse-engineering/crypto-and-keys.md), [`pairing & robot`](docs/reverse-engineering/pairing-and-robot.md), [`QR format`](docs/reverse-engineering/qr-format.md), [`app structure`](docs/reverse-engineering/app-structure.md)
- **Features** — the complete parent-app feature catalog ([`docs/features/`](docs/features/))
- **Guides** — task how-tos for owners ([`docs/guides/`](docs/guides/))
- **Community** — [`the existing revival landscape`](docs/community-research.md) (OpenMoxie et al.)

## Legal & ethics

An independent **interoperability and repair** project for hardware people already own, built by
**clean-room reverse engineering** of the freely-distributed app solely to restore function to
abandoned devices. It ships **no** Embodied code, assets, firmware, or binaries. No affiliation
with or endorsement by Embodied Inc. "Moxie" is used only to identify the hardware this software
interoperates with. Children's data is handled exactly as the original app did — **end-to-end
encrypted, with the keys held by you**.

## License

MIT — see [`LICENSE`](LICENSE).
