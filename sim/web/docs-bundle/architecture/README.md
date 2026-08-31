# 🏗️ Architecture

How all the pieces fit together.

- [`overview.md`](overview.md) — the two-channel model, the components, the appliance vision, privacy.
- [`revival-path.md`](revival-path.md) — the exact steps + firmware gate to revive a robot (the 3-QR sequence).
- [`rest-api-contract.md`](rest-api-contract.md) — **Channel 1, the control plane**: the REST services
  our parent-app server exposes (auth, children, pairing, robot settings) + the minimum-viable-server path.
- [`mqtt-and-conversation.md`](mqtt-and-conversation.md) — **Channel 2, the robot cloud**: endpoint QR, MQTT broker,
  topics, conversation flow, and the local-AI plug-in points (Phases 2–3).
- [`ai-seam.md`](ai-seam.md) — **the interface contract** for the three seams a backend fills (STT in /
  brain-RemoteChat / TTS out): exact wire shapes + a conformance checklist. Build any AI into Moxie from this.
- [`moxie-as-a-platform.md`](moxie-as-a-platform.md) — **the SDK**: how any AI/game drives Moxie as an avatar.
- [`static-experience.md`](static-experience.md) — the **combined static site**: parent app + simulator +
  cloud UI on Cloudflare Pages, and the roadmap to the real end-to-end system.
- [`moxie-ecosystem.md`](moxie-ecosystem.md) — the full self-hostable stack: brain, voice, ears, liveness.
- [`sil-and-cicd.md`](sil-and-cicd.md) — the simulator's design + the test/CI layers that guard it.
- [`vision.md`](vision.md) — can Moxie *see*? Camera access reality + a local OpenCV/VLM vision stack (research).

---
📖 [Docs index](../README.md) · [Back to top](../../README.md)
