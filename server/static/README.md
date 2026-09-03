# 📱 `server/static` — the mobile web client

The parent-app UI your phone loads over the LAN. Vanilla HTML/CSS/JS — **no build step, no external
dependencies** (works fully offline). Served at `/` by the FastAPI server.

- `index.html` — the setup flow (login → child + Wi-Fi → QR → paired), then the Moxie tab: 🔐 Robot
  access (permit a pending robot), live state, ⚙️ Settings, 📈 Insights, 🛡️ Safety,
  🎨 Moxie's look (pick the face layers — see the [guide](../../docs/guides/moxies-look.md)),
  🎭 Be Moxie (drive the robot as a remote grown-up),
  📅 Today's plan (the day the robot is served, with the recommender's *"why this activity today"*
  line under each entry — read-only; the plan is changed from ⚙️ Settings),
  🧠 What Moxie remembers (browse + erase long-term memory),
  🎚️ Voice (pick the Speech and Listening engines from what this appliance can really use —
  the gateway's models discovered live, the local Piper voices and whisper sizes installed on the
  box, and the built-ins; see the [TTS guide](../../docs/guides/litellm-tts-setup.md) and the
  [STT guide](../../docs/guides/litellm-stt-setup.md)).
- `app.js` — talks to the server's `/local/*` and `/api/*` endpoints.
- `style.css` — mobile-first, light/dark aware.

The QR image itself is rendered server-side (`/local/pairing/qr.png`) so the client stays tiny.

---
📖 [Back to top](../../README.md) · [Server README →](../README.md)
