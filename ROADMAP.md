# Roadmap — end-to-end Moxie revival

The complete arc from "bricked robot" to "Moxie talks again, fully local." Each phase is
independently useful; we don't stop at "it works."

Legend: ✅ done · 🔨 in progress · ⬜ planned

---

## Phase 0 — Reverse engineering  ✅
Completely map the parent app so nothing is guessed.
- ✅ Decompile + map the REST API, auth, crypto, pairing, QR formats, app structure.
- ✅ Survey the community (OpenMoxie) so we build the gap, not a duplicate.
- 🔨 **Exhaustive feature catalog** — every user-facing *and* hidden/developer feature.
- 🔨 **Robot lifecycle** — pairing, unpair, **factory reset**, restore/backup, reboot, OTA.
- 🔨 **MQTT + conversation protocol** spec (from OpenMoxie's real Embodied protobufs).

## Phase 1 — Parent app (control plane)  ✅ working
Recreate the app owners used to set up Moxie — client + server, local, account-free.
- ✅ REST server: login, users, children, robots, pairing-info, secret-key-collection.
- ✅ Mobile web client served on the LAN.
- ✅ Clean-room **Wi-Fi pairing QR** — **verified: a real Moxie scanned it and joined Wi-Fi.**
- ✅ Deterministic recovery-key crypto (Argon2id → Ed25519/X25519/secretbox).
- ✅ Hardware-free pairing test (`simulate-robot-scan`).
- ⬜ Wire the feature catalog into the web UI (child profile depth, robot settings, insights view).
- ⬜ **Factory reset / unpair** actions in the web UI (see `docs/features/`).
- ⬜ Compatibility mode for pointing the *original* Android APK at this server.

## Phase 2 — Get Moxie onto our cloud  🔨
Move the robot off the dead Embodied cloud and onto our box.
- ⬜ **Endpoint-config QR** generator (the "second QR" a firmware-801/803 Moxie waits for) —
  `{"debug":{"command":"om","param":"<ServiceConfiguration2>"}}`, pointing at our MQTT host.
- ⬜ **MQTT broker** (mosquitto, TLS :8883, self-signed CA — works on firmware 24.10.803).
- ⬜ Detect robot connect/disconnect; push the initial robot config (`pairing_status`, schedule, settings).
- ⬜ Firmware-version guidance + the 801→803 upgrade note (see `docs/architecture/revival-path.md`).

## Phase 3 — Make Moxie talk  ⬜
The live experience, powered entirely by local AI.
- ⬜ **Conversation engine** — RemoteChatRequest/Response turns over MQTT.
- ⬜ **Local STT** — Whisper (e.g. faster-whisper) on the GPU; ZMQ-over-MQTT audio bridge.
- ⬜ **Local LLM** — any local model via an OpenAI-compatible endpoint (LiteLLM / vLLM / Ollama / LM Studio); OpenAI itself only as an optional fallback. **Never hard-wired to a vendor.**
- ⬜ **Local TTS** — on-device voice synthesis.
- ⬜ **Behavior markup** — the text→Moxie-expression engine so Moxie moves/emotes, not just speaks.

## Phase 4 — The full experience  ⬜
- ⬜ Content **modules/activities** (Daily Missions, Reading, Tips, jokes, breathing, …).
- ⬜ **Insights** populated from real robot activity (the MQTT data source).
- ⬜ Schedules, missions, rewards, "sensitive conversations."
- ⬜ Puppet / telehealth remote-control mode.

## Phase 5 — Packaging  ⬜
Make it something a non-engineer can run.
- ⬜ One-command deploy (Docker Compose / a single installer) for **any GPU box** — gaming PC, home server, Jetson Orin.
- ⬜ Optional hosted deployment for owners who can't self-host (multi-tenant, still zero-knowledge).
- ⬜ Setup wizard, health dashboard, backup/restore of the whole appliance.

---

### Deployment target
Designed to run on **any machine with a CUDA GPU** — a gaming PC, a home server, or an NVIDIA
Jetson Orin — as a self-contained appliance. Cross-platform (Linux / Windows / macOS for the
control plane; GPU box for the AI layer). No internet required at runtime.
