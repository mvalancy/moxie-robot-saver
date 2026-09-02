# 📱 `server/static` — the mobile web client

The parent-app UI your phone loads over the LAN. Vanilla HTML/CSS/JS — **no build step, no external
dependencies** (works fully offline). Served at `/` by the FastAPI server.

- `index.html` — the setup flow (login → child + Wi-Fi → QR → paired), then the Moxie tab: 🔐 Robot
  access (permit a pending robot), live state, ⚙️ Settings, 📈 Insights, 🛡️ Safety, 🧠 What Moxie
  remembers (browse + erase long-term memory).
- `app.js` — talks to the server's `/local/*` and `/api/*` endpoints.
- `style.css` — mobile-first, light/dark aware.

The QR image itself is rendered server-side (`/local/pairing/qr.png`) so the client stays tiny.

---
📖 [Back to top](../../README.md) · [Server README →](../README.md)
