# 🔬 Reverse-engineering (source of truth)

Clean-room maps of the Moxie system, in two halves. **Phone side:** the decompiled parent app
(`com.embo.embodied.parent` v2.2.2). **Robot side:** the on-device firmware (RK3288 Android 9) and
its `bo-*` apps, read from the factory partition images. No Embodied source is included — only
observed facts and schemas reconstructed from shipped binaries. Everything else in the repo derives
from these.

```mermaid
flowchart TB
    subgraph phone["📱 Parent app (phone side)"]
      rest["🌐 rest-api"]
      crypto["🔐 crypto-and-keys"]
      pair["🔗 pairing-and-robot"]
      qr["🎫 qr-format"]
      struct["🧩 app-structure"]
    end
    subgraph robot["🤖 Firmware (robot side)"]
      fw["🧱 firmware-image"]
      ipc["🧠 robot-ipc-protocol"]
      qrc["🎫 qr-commands"]
      hw["🦾 hardware-map"]
      fac["🏭 factory-provisioning"]
      proto["📦 recovered-proto/"]
    end
    qr -.same QR.- qrc
    ipc --> proto
    hw --> proto
    classDef d fill:#e3eaf2,stroke:#607d8b,color:#263238;
    class rest,crypto,pair,qr,struct,fw,ipc,qrc,hw,fac,proto d;
```

### Phone side — the parent app

- [`rest-api.md`](rest-api.md) — every endpoint, the passwordless-email→OAuth flow, headers, token shapes.
- [`crypto-and-keys.md`](crypto-and-keys.md) — the one 32-byte seed (Argon2id) → Ed25519/X25519/secretbox, recovery keys, E2E encryption.
- [`pairing-and-robot.md`](pairing-and-robot.md) — the pairing handshake, Wi-Fi provisioning, robot control API.
- [`qr-format.md`](qr-format.md) — the exact pairing-QR wire format (protobuf + legacy JSON), as the phone emits it.
- [`app-structure.md`](app-structure.md) — manifest, components, third-party SDKs, package inventory.

### Robot side — the firmware (for running custom software on the device)

- [`firmware-image.md`](firmware-image.md) — RK3288 / Android 9 partition layout, verified boot (AVB), security posture, installed apps, and **how to unlock & flash custom firmware**.
- [`ota-and-recovery.md`](ota-and-recovery.md) — the A/B OTA machinery, payload signing gate, and an **honest map of no-disassembly upgrade vectors** for reviving old robots.
- [`robot-ipc-protocol.md`](robot-ipc-protocol.md) — the on-device **ZeroMQ + protobuf** message bus that wires the modules together; the module map and behavior-command markup.
- [`cloud-protocol.md`](cloud-protocol.md) — the robot↔backend surface (REST `client-service`, MQTT topics, Deepgram STT, the chat envelope) — **what a self-hosted server must implement**.
- [`qr-commands.md`](qr-commands.md) — the **complete QR grammar** the robot scans (pairing / VPN / debug-factory commands), read from `bo-wifi`.
- [`hardware-map.md`](hardware-map.md) — motors, touch/switch/IMU sensors, LED face patterns, and power rails, from the MCU protobufs.
- [`factory-provisioning.md`](factory-provisioning.md) — the production-line apps, serial/part grammar, and the **factory secret** getters (and how to recover them).
- [`recovered-proto/`](recovered-proto/) — **120 `.proto` files** reconstructed from the robot binaries; the machine-readable protocol.

---
📖 [Docs index](../README.md) · [Back to top](../../README.md)
