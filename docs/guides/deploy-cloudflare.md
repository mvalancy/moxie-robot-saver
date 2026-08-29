# ☁️ Deploying the SIL to Cloudflare Pages

> **What this is.** How to publish the [Moxie simulator](../../sim/web/) as a static site on Cloudflare
> Pages so anyone can see a 3D Moxie in a browser with **zero install** — and, honestly, **what does and
> doesn't survive** the move off localhost. Robot behaviour is grounded in firmware
> **v3.6.4-Zephyr / OTA v24.10.803**.

## TL;DR

`sim/web/` is **already a static bundle** — 1.9 MB, no build step, all deps vendored (three.js, MQTT.js,
Inter + JetBrains Mono). Point Cloudflare Pages at it and it works. What changes is the *live* half:

| Feature | Static site? | Why |
|---|---|---|
| 3D Moxie, rig, liveness, expressions, HUD | ✅ works | pure client-side WebGL |
| **Play demo** (canned session replay) | ✅ works | replays `sessions/demo.json`, no server |
| Hand controls (motors, face, LED, scene light) | ✅ works | direct `window.moxie` calls |
| **Piper TTS** (Moxie's voice) | ✅ works | **pre-rendered clips** (`audio/index.json`) |
| Child's voice (audible) | ✅ works | pre-rendered too — both sides of the conversation |
| **Conversation** (talk → reply → gestures) | ✅ works | **stub brain** emits real behavior markup |
| Mic button | ✅ degrades | falls back to a **scripted child line** (no STT model) |
| **Revival QR** (re-home a real robot) | ✅ works | payloads are plain JSON, built client-side |
| **Setup page** (`setup.html`) — parent-app basics | ✅ works | phone-first Wi-Fi + server QR, no server |
| **Docs explorer** (`docs.html`) — every RE doc + mermaid | ✅ works | reads committed `docs-bundle/` + `docs-index.json` |
| **Landing hub** (`hub.html`) — the front door | ✅ works | links the three surfaces |
| **Cloud console** (`cloud.html`) — parent dashboard | ✅ works | read-only demo from `fixtures/cloud.json` |
| **Live bus** (a REAL robot connecting) | ❌ self-host | needs your MQTT broker + TLS |

The one thing on that list that isn't a demo is the **revival QR**: the codes that re-home a robot are
plain JSON, so the static page generates the real thing. A parent with a dead Moxie and no computer can
open the Pages URL on a phone, tap **Make**, and hold it up to the camera.

**So the static deploy is a complete, voiced, animated demo** — you can talk to Moxie and it answers,
speaks, gestures and shows symbols with **no server at all**. The stubs use the *same protocol shapes*
as the real services, so **plugging in the real backend is transparent**: if a broker/TTS/STT is
reachable, it's used; if not, the stubs take over. The real ecosystem stays self-hostable
([revival guide](revive-your-moxie.md)).

> **Production domain:** this site is built to be served at **`moxie.mattvalancy.com`** (canonical URLs
> and Open Graph tags point there). Set that as a custom domain on the Pages project; the pages are
> otherwise origin-relative and work under any host.

## 1. Deploy the static bundle

### Easiest: point Cloudflare Pages at this repo (no build step)

The repo ships a [`wrangler.toml`](../../wrangler.toml) with `pages_build_output_dir = "sim/web"`, so
connecting Pages to the repo needs no configuration:

1. Cloudflare dashboard → **Workers & Pages → Create → Pages → Connect to Git**, pick this repo.
2. Build settings: **Framework preset = None**, **Build command = (empty)**, **Build output directory =
   `sim/web`** (already set by `wrangler.toml`).
3. **Save and Deploy.** Every push to `main` redeploys automatically.

It works because the site is a **pre-built static bundle**: no compile step, all deps vendored (three.js,
mqtt.js, marked, mermaid, highlight.js, qrcode, Inter/JetBrains fonts), and the docs bundle
(`sim/web/docs-bundle/`) is committed. 8 MB, ~100 files, nothing over Cloudflare's 25 MB/file limit.

Set the custom domain to **`moxie.mattvalancy.com`** (canonical + OG tags already point there).

### CLI alternative

```sh
# Wrangler (or connect the repo in the Cloudflare dashboard and set the output dir)
npx wrangler pages deploy sim/web --project-name moxie-sil
```

No build command; **output directory = `sim/web`**. Nothing to install — deps are vendored, which is
also why the site works offline once loaded.

**Headers** — add `sim/web/_headers` (Cloudflare Pages reads it):

```
/vendor/*
  Cache-Control: public, max-age=31536000, immutable
/audio/*
  Cache-Control: public, max-age=31536000, immutable
/*.js
  Cache-Control: public, max-age=3600
/index.html
  Cache-Control: no-cache
```
Vendored libs, fonts and pre-rendered audio are content-addressed and never change → cache them for a
year. Keep `index.html` uncached so redeploys take effect immediately.

**Landing page.** On localhost and by default on Pages, `/` is the **simulator** (`index.html`). To make
the **hub** (`hub.html`) your public front door instead, add a `sim/web/_redirects`:

```
/    /hub.html    200
```

That rewrite serves the hub at `/` on Pages only; the simulator stays reachable at `/index.html`, which
is exactly what the hub's "Simulator" card links to. Localhost is unaffected (the dev server ignores
`_redirects`), so your `http://localhost:8080/` muscle memory still opens the simulator.

## 2. Pre-cache the audio (both sides of the conversation)

This is the key trick for a static demo: **the conversation is scripted, so every line is known in
advance** — render the audio at build time and ship it as files. No TTS service, no STT, no LLM.

### Robot side (Moxie's lines) — Piper, at build time
Every `output.text` in a session file gets rendered once and committed:

```sh
python3 sim/tools/prerender_audio.py sim/web/sessions/demo.json --out sim/web/audio
# -> sim/web/audio/moxie/<sha1-of-text>.wav  + an index.json manifest
```

At runtime `audio.js` looks the line up in the manifest and plays the file instead of calling `/tts`;
if there's no match **and** no TTS service, it falls back to silent text (the avatar still animates and
the transcript still fills in).

### Child side (the "user" turns) — also pre-rendered
The child's lines are equally scripted, so render them too (a different Piper voice, e.g.
`en_US-amy` for Moxie and a lighter voice for the child) into `sim/web/audio/child/`. Playing both
sides makes the demo feel like a real exchange rather than a monologue — and it's what lets the whole
thing run with **no microphone and no STT**.

> Mic input still works on a static site (`getUserMedia` over HTTPS), and with no STT service the
> **Listen** button falls back to a **scripted child line** (cycling the lines that have pre-rendered
> audio), so the demo conversation still runs end-to-end.

### Pre-warming
Add `<link rel="preload" as="fetch">` for the manifest and `as="audio"` for the first few clips, and
optionally a small service worker that caches `/audio/*` and `/vendor/*` on first load. After that the
demo runs entirely from cache — good, because Pages has no origin compute to fall back on.

## 3. Optional: keep the live features on Cloudflare

If you want more than a canned demo without self-hosting:

- **TTS on demand** → a **Cloudflare Worker** calling a hosted TTS API, or Workers AI. (Piper itself is a
  native binary; it does **not** run in a Worker — that's why build-time pre-rendering is the clean path.)
- **STT** → a Worker proxying a speech API, returning the robot's
  [`DeepgramResponse`](../reverse-engineering/perception-pipeline.md#stt-response-wire-format-deepgramresponse)
  shape so no client change is needed.
- **LLM** → a Worker in front of your model, speaking the same
  [`RemoteChat`](../reverse-engineering/cloud-protocol.md) envelope.
- **Live bus** → Cloudflare **Durable Objects** can hold WebSocket state, but the robot speaks **MQTT**;
  a real robot needs a reachable MQTT broker, which Pages/Workers don't provide. **Self-host the broker.**

## 4. What to configure per-environment

`audio.js` / `mic.js` default their endpoints to the **page's own host**, which is right for localhost
and compose but wrong for Pages. For a static deploy either:
- leave them unset and rely on **pre-cached audio** (recommended), or
- set them (via the HUD fields, persisted in `localStorage`) to your Worker/tunnel URL.

## 5. How the stubs work (and how the real server plugs in)

[`sim/web/stub.js`](../../sim/web/stub.js) stands in for the three server pieces using the **same
protocol shapes**, so nothing else in the app changes:

| Piece | Stub | Real service |
|---|---|---|
| Brain | canned replies **with real `<mark cmd:…>` markup** (mood · gesture · `icons-v2`) | `mqtt/` supervisor + `LLMApp` |
| TTS | pre-rendered clips from `audio/index.json` | `sim/tts/server.py` (Piper) |
| STT | matches to a scripted child line | `sim/stt/server.py` (faster-whisper) |

**Precedence is automatic:** `audio.js` plays a pre-cached clip if one exists, else calls the live TTS;
`bridge.js` publishes to the broker when connected, else answers from the stub brain; `mic.js` posts to
the STT service, else falls back to a scripted line. Point the HUD's endpoint fields at a real backend
and the stubs simply stop being reached.

Verified with every backend port blocked: a typed turn produced a child turn **and** a spoken Moxie
reply with gesture + icon, zero console errors.

## 6. Honest limits

- **The stub brain doesn't think.** It pattern-matches; only a real LLM backend converses freely.
- **A real robot cannot connect to a CDN.** Re-homing needs *your* MQTT broker and TLS cert.
- **The demo is a shop window,** not the product. The product is the self-hosted stack in
  [`sim/`](../../sim/) + [`mqtt/`](../../mqtt/) — that's what revives a robot.
- Cloudflare Pages has a **25 MB per-file** limit; pre-rendered WAVs are small, but convert long
  sessions to **Opus/MP3** to keep the bundle lean.

---
📖 [Simulator](../../sim/README.md) · [Revive your Moxie](revive-your-moxie.md) · [Ecosystem plan](../architecture/moxie-ecosystem.md) · [Docs index](../README.md)
