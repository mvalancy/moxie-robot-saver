# Roadmap — end-to-end Moxie revival

**Mission: an end-to-end system to revive _any_ Moxie robot, fully local.** The complete arc from
"bricked robot" to "Moxie talks and sees again" — and, for units too old for the software path,
whatever firmware-level work it takes. Each phase is independently useful; we don't stop at "it works."

Legend: ✅ done · 🔨 in progress · ⬜ planned · 🔬 research

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

## Milestone — the experience, hosted online (static) 🔨
A **combined parent app + simulator + example cloud UI** on **Cloudflare Pages** — "just the basics
online," growing into the real end-to-end system. Deploy root is `sim/web/` (shared vendored deps).
Full map: [`docs/architecture/static-experience.md`](docs/architecture/static-experience.md).
- ✅ **Simulator** — the 3D Moxie with a stub brain + pre-rendered audio; talks with no server.
- ✅ **Parent app (basics)** — [`sim/web/setup.html`](sim/web/setup.html): phone-first, server-free
  Wi-Fi + server QR, built client-side by [`sim/web/qr.js`](sim/web/qr.js) (byte-identical to the CLI).
- ✅ **Revival QR in-browser** — plain-JSON QR types generated client-side (no install).
- ✅ **Example cloud UI** — [`sim/web/cloud.html`](sim/web/cloud.html): read-only parent console (child,
  Daily Missions & rewards, conversation + activity log, robot, notifications) from fixture JSON whose
  shapes mirror the real REST API + MQTT content model.
- ✅ **Landing hub** — [`sim/web/hub.html`](sim/web/hub.html): one front door presenting the three surfaces.
- ⬜ **Cloudflare Pages deploy** — wrangler/dashboard, `_headers`, audio pre-cache (both sides).

---

## Parallel research tracks 🔬
Longer-horizon work that runs alongside the phases. In-scope, honestly hard, documented as we go.

### Track A — Moxie sees (camera + vision)
Give Moxie real vision, feeding the conversation.
- ⬜ Subscribe to on-device vision **events** over MQTT (`eb-found-face`, `eb-lost-target`, marker
  events) — the non-invasive starting point (semantic only; no pixels).
- 🔬 An **external camera** + local vision stack (OpenCV → fast detector → local **VLM** on an
  OpenAI-compatible endpoint), fused back into the conversation, for a true "Moxie sees" experience.
- 🔬 Investigate any path to the robot's **own** camera frames (blocked in stock firmware today).
- Detail + honest feasibility: [`docs/architecture/vision.md`](docs/architecture/vision.md).

### Track B — Older robots (firmware)
Bring back units older than firmware 24.10.801, which lack the custom-endpoint (QR relocation) path.
- ⬜ Obtain / relay the signed **803 OTA** for 801 units.
- 🔬 **Firmware acquisition & analysis** — dump and study a system image from a robot we own.
- 🔬 **Bootloader / Verified Boot** investigation (unlock, downgrade, or signed-update paths).
- 🔬 Hardware-assisted methods (serial/eMMC/CSI) for **research units only**, last resort.
- Detail + the lockdown facts: [`hardware/firmware-and-older-robots.md`](hardware/firmware-and-older-robots.md).

---

### Deployment target
Designed to run on **any machine with a CUDA GPU** — a gaming PC, a home server, or an NVIDIA
Jetson Orin — as a self-contained appliance. Cross-platform (Linux / Windows / macOS for the
control plane; GPU box for the AI layer). No internet required at runtime.
