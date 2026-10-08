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
  box, and the built-ins; see the [TTS guide](../../docs/guides/gateway-voice-and-ears.md) and the
  [STT guide](../../docs/guides/gateway-voice-and-ears.md)),
  📦 Content (packs and the editor), and 💬 Try it (talk to Moxie's real brain with no robot: pick
  a brain or an installed conversation, type what a child might say, and see her words, face, moves
  and actions — a preview that publishes nothing and remembers nothing; see
  [content authoring §5.3](../../docs/architecture/backlog/content-authoring.md)).
- [`js/`](js/) — the scripts, one per group of cards, loaded in order (no bundler); they talk
  to the server's `/local/*` and `/api/*` endpoints.
- `style.css` — mobile-first, light/dark aware.

The QR image itself is rendered server-side (`/local/pairing/qr.png`) so the client stays tiny.

## What is actually tested in a browser

[`sim/test_console_insights.mjs`](../../sim/test_console_insights.mjs) serves this folder
statically, answers every `/local/*` call at the browser (no fastapi, no supervisor), and sweeps
all six render paths of 📈 Insights — including the two-click **Erase history**, asserted on the
intercepted `DELETE`; its teeth mutate [`js/insights.js`](js/insights.js).

[`sim/test_console_tryit.mjs`](../../sim/test_console_tryit.mjs) does the same for 💬 Try it: the
card shows with no robot, typing alone never calls the brain, one click (or Enter) is one call
carrying the session, a refusal is shown without advancing it, and Start over or another brain wins
over an answer still on its way; its teeth mutate [`js/tryit.js`](js/tryit.js).

Every other card is asserted by Python route tests and source pins
([`test_console_roundtrip.py`](../../sim/tests/test_console_roundtrip.py)), which prove what the
server answers but not that a button wires itself up. Still uncovered by any browser suite: 🔐
Robot access, ⚙️ Settings, 🛡️ Safety, 🎨 Moxie's look, 🎭 Be Moxie, 📅 Today's plan, 🧠 memory,
🎚️ Voice, 📦 Content, 🧠 Brain, and the returning-parent entry path.

---
📖 [Back to top](../../README.md) · [Server README →](../README.md)
