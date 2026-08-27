# Moxie Robot — Parent App (local, self-hosted)

**Bring a Moxie robot back to life without Embodied's dead cloud.**

Embodied Inc. shut down in December 2024, taking its servers with it and bricking every
Moxie robot in the field. This project is a **clean-room reimplementation of the Moxie
"parent app"** — both the **client** (a mobile-friendly web app) and the **server** (the
REST backend the app used to talk to) — so you can run the *entire* parent-app experience
**locally, on your own computer, with no account and no remote service**.

Your phone opens the web app over your LAN (or Tailscale), and you use it to set up your
child's profile, hand Moxie your Wi-Fi, and generate the **pairing QR code** you hold up to
Moxie's camera — exactly like the original app did.

> **Why:** unlock every Moxie so kids get their robot back, and keep an genuinely lovely
> piece of hardware out of the landfill. Local-first and **zero-knowledge by design** — the
> original app end-to-end-encrypts child data, and so does this, so *you* hold the keys.

---

## What works today

- ✅ **Full account-free parent-app server** — a faithful reimplementation of
  `client-service-api.embodied.com` (login, users, children, robots, pairing, key escrow).
- ✅ **Mobile web client** — open it from your phone, set up a child + Wi-Fi, generate the QR.
- ✅ **Clean-room pairing-QR generator** — byte-for-byte reconstruction of the app's
  `"PA"+protobuf` format, carrying Wi-Fi creds + the Ed25519 pairing seed.
- ✅ **Deterministic recovery-key crypto** — Argon2id seed derivation reproduced exactly
  (diceware phrase → seed → Ed25519/X25519/secretbox), verified against the decompiled app.
- ✅ **Hardware-free test loop** — a "simulate robot scan" endpoint completes the whole
  pairing flow with no physical robot, so you can verify everything end to end.

## What this is *not* (yet)

Making Moxie **talk** (conversations, activities, STT/LLM) is a **separate layer**: the robot's
live channel is **MQTT/IoT**, not this REST API. That side is covered by
[**OpenMoxie**](https://github.com/jbeghtol/openmoxie) and is on our roadmap to integrate.
See [`docs/COMMUNITY_RESEARCH.md`](docs/COMMUNITY_RESEARCH.md) for the full landscape and how
the two halves fit together.

```
        REST / HTTPS (this repo)                 MQTT / TLS (OpenMoxie + your LLM)
 phone ─────────────────────► local server   Moxie ─────────────────────► local broker
  web app   account, child,    (this project)         conversations, STT,   + LiteLLM / AI
            Wi-Fi QR, pairing                          activities, "talking"
```

---

## Quick start

```bash
git clone https://github.com/mvalancy/moxie-robot-parent-app.git
cd moxie-robot-parent-app
pip install -r server/requirements.txt
python server/run.py                 # serves on 0.0.0.0:8080
```

Then open **`http://<this-computer-ip>:8080`** from your phone (same LAN, or Tailscale).

1. Enter any email → **Start**.
2. Add your child's name, your **Wi-Fi** details (2.4 GHz recommended), → **Generate pairing QR**.
3. Put Moxie on its pairing screen and hold the QR up to its camera.
4. **Save the recovery phrase** it shows you.

No robot handy? Use the **"Simulate robot scan"** button to watch pairing complete.

### Command-line QR tool (no server needed)

```bash
python tools/pairing/moxie_pair.py --ssid HomeWiFi --password 's3cr3t' --band 24g --out qr.png
```

---

## Repository layout

| Path | What |
|------|------|
| [`server/`](server/) | The local parent-app server (FastAPI) — REST API + web client + crypto |
| [`server/static/`](server/static/) | The mobile web client (vanilla JS, no build step) |
| [`tools/pairing/`](tools/pairing/) | Clean-room QR codec + CLI (`moxie_qr.py`, `moxie_pair.py`) |
| [`docs/`](docs/) | The reverse-engineering spec (see below) |

## Documentation

- [`docs/REST_API.md`](docs/REST_API.md) — every endpoint, the OAuth/email-code auth flow, headers, token shapes.
- [`docs/CRYPTO_AND_KEYS.md`](docs/CRYPTO_AND_KEYS.md) — the one-seed key system, Argon2id params, recovery keys, E2E encryption.
- [`docs/PAIRING_AND_ROBOT.md`](docs/PAIRING_AND_ROBOT.md) — the pairing handshake, Wi-Fi provisioning, robot control API.
- [`docs/pairing-qr-format.md`](docs/pairing-qr-format.md) — the exact QR wire format (JSON + protobuf modes).
- [`docs/APP_STRUCTURE.md`](docs/APP_STRUCTURE.md) — manifest, components, SDKs, package inventory.
- [`docs/COMMUNITY_RESEARCH.md`](docs/COMMUNITY_RESEARCH.md) — **the existing Moxie-revival community** (OpenMoxie et al.) and where this project fits.

## Roadmap

- [ ] Integrate the MQTT/robot layer (OpenMoxie-compatible) so Moxie actually talks — pluggable to a local LLM (e.g. LiteLLM).
- [ ] Optional hosted deployment (e.g. `my-moxie-robot.com`) for owners who can't self-host — multi-tenant and still zero-knowledge.
- [ ] Insights/activity views once the MQTT data source is wired in.
- [ ] Compatibility mode for pointing the *original* Android APK at this server.

## Legal & ethics

This is an independent **interoperability and repair** project for hardware people already own,
built by **clean-room reverse engineering** of the freely-distributed app for the sole purpose
of restoring function to abandoned devices. It ships **no** Embodied code, assets, or binaries.
No affiliation with or endorsement by Embodied Inc. "Moxie" is used only to identify the
hardware this software interoperates with.

## License

MIT — see [`LICENSE`](LICENSE).
