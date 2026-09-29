# The static site

`sim/web/` is one static site that works on any CDN (it is deployed on Cloudflare Pages). It gives
anyone a taste of Moxie with no install, and it gives an owner with only a phone the QR codes to revive
a robot.

## The pages

| Page | What it is |
|---|---|
| [`index.html`](../../sim/web/index.html) | The landing hub that links everything below. |
| [`sim.html`](../../sim/web/sim.html) | The 3D Moxie, driven by the real protocol. With no backend it uses a scripted brain and pre-rendered voice; on a deployment with a gateway it uses a real brain, voice and ears. |
| [`setup.html`](../../sim/web/setup.html) | The phone flow for re-homing a real robot: a Wi-Fi QR and a server QR, both built in the browser by [`qr.js`](../../sim/web/qr.js). |
| [`cloud.html`](../../sim/web/cloud.html) | A read-only example parent console (child, missions, conversation log, robot status) from [`fixtures/cloud.json`](../../sim/web/fixtures/cloud.json), whose shapes mirror the real REST and MQTT models. |
| [`docs.html`](../../sim/web/docs.html) | The docs explorer: every doc in `docs/` with diagrams, search and deep links, from a bundle built by [`build_docs_bundle.py`](../../sim/tools/build_docs_bundle.py). |

All pages share one vendored `vendor/` tree (three.js, MQTT.js, marked, mermaid, highlight.js, fonts),
so nothing loads from third-party hosts.

## What needs a server

- **Static:** anything that makes a QR code, animates the avatar, or replays a scripted conversation.
  The revival QR codes are plain JSON ([QR commands](../reverse-engineering/protocol/qr-commands.md)).
- **Same-origin Functions** ([`functions/`](../../functions/README.md)): the live brain, voice and ears
  on a hosted deployment, behind rate limits and a scripted fallback.
- **Your own backend:** anything that talks to a real robot (it needs an MQTT broker over TLS, which a
  CDN cannot be) or stores a real account and child profile (the [`server/`](../../server/) app).

Each static surface has a live counterpart, and the client uses it when it is reachable:

| Static | Live |
|---|---|
| `setup.html` (QR codes only) | Full pairing, recovery phrase and child account in [`server/`](../../server/) |
| Scripted brain | The [`mqtt/`](../../mqtt/) supervisor with your LLM, or the hosted gateway |
| Pre-rendered voice | Piper, Whisper or a gateway ([guide](../guides/gateway-voice-and-ears.md)) |
| Example console | The real parent console on your backend |

Deploying: [Cloudflare guide](../guides/deploy-cloudflare.md).

---
[Architecture index](README.md) · [Revive your Moxie](../guides/revive-your-moxie.md) · [Architecture overview](overview.md)
