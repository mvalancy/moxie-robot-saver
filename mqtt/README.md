# `mqtt/` — the robot cloud (Phase 2–3)

This is the second half of the revival: the service the **robot** connects to, replacing Embodied's
MQTT/IoT cloud. It is not built yet — this directory holds the plan; the detailed spec lands in
[`../docs/features/`](../docs/features/) and the architecture docs.

## What goes here
- **Endpoint-config QR generator** — the "second QR" a firmware-801/803 Moxie waits for after Wi-Fi
  (`{"debug":{"command":"om","param":"<ServiceConfiguration2>"}}`), pointing the robot at this box.
- **MQTT broker** — mosquitto, TLS on :8883 with a self-signed CA (works on firmware 24.10.803).
- **Device manager** — detect robot connect/disconnect, push initial config, mark paired.
- **Conversation engine** — RemoteChatRequest/Response turns; the audio→STT→LLM→markup→TTS→speak loop.

## How it relates to `server/`
`server/` (the parent app) issues the **Wi-Fi QR** and owns account/child/robot identity. `mqtt/`
issues the **endpoint QR** and runs the live conversation. They share the same account database and
run on the same machine. See [`../docs/architecture/overview.md`](../docs/architecture/overview.md).

## Status
🔨 Being specced now (from OpenMoxie's real Embodied protobufs). See [`../ROADMAP.md`](../ROADMAP.md) Phase 2–3.
