# 📱 `server/static` — the mobile web client

The parent-app UI your phone loads over the LAN. Vanilla HTML/CSS/JS — **no build step, no external
dependencies** (works fully offline). Served at `/` by the FastAPI server.

- `index.html` — the setup flow (login → the Wi-Fi-only code → the server code → ➕ Add to my
  account on the 🤖 Moxie tab; see the [bench runbook](../../docs/guides/bench-runbook.md)), then the
  Moxie tab: 🔐 Robot access (permit a pending robot, or add it to your account), live state,
  ⚙️ Settings, 📈 Insights, 🛡️ Safety,
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

[`sim/test_robot_claim.mjs`](../../sim/test_robot_claim.mjs) covers the bench-day flow: the Wi-Fi
tab's code is Wi-Fi only unless the pairing-key box is ticked; a robot on no account is offered ➕ Add
to my account on *No Moxie paired yet* and beside Permit on its pending row in 🔐 Robot access (one
click, one claim, never automatic); the page says why when it cannot be offered (this account
already has a robot, or another account has this one) or when the supervisor cannot be asked; each
refusal stays on screen in the server's words, also when the redraw after it hides the card that
was clicked; the answer to a click that added the robot stays through the 📶 Wi-Fi tab's poll and a
re-opened tab; and the tab notices a robot that arrives while it is open. Its teeth mutate
[`js/core.js`](js/core.js), plus one mutation each of [`index.html`](index.html) and
[`js/settings.js`](js/settings.js).

[`sim/test_console_settings.mjs`](../../sim/test_console_settings.mjs) covers ⚙️ Settings' time
zone: the field shows the zone in force (a house rule, this robot's own, or the server's
`MOXIE_TIMEZONE`) and the hint names which; a save sends `timezone_id` for one robot or as a house
rule, and only when the parent changed it; while no zone is set and the browser is in another one, a
line outside Settings names both zones and one click saves the browser's as a house rule; a save the
supervisor could not write says so, and a refused zone is said in the server's words. Its teeth
mutate [`js/settings.js`](js/settings.js).

Every other card is asserted by Python route tests and source pins
([`test_console_roundtrip.py`](../../sim/tests/test_console_roundtrip.py)), which prove what the
server answers but not that a button wires itself up. Still uncovered by any browser suite: 🔐
Robot access's Permit, Revoke and let-any-robot switch, ⚙️ Settings' other fields (volume,
bedtime, wake alarm, scheduled activity), 🛡️ Safety, 🎨 Moxie's look,
🎭 Be Moxie, 📅 Today's plan, 🧠 memory, 🎚️ Voice, 📦 Content, 🧠 Brain, and the returning-parent
entry path.

---
📖 [Back to top](../../README.md) · [Server README →](../README.md)
