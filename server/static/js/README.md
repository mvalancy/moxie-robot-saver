# 📜 `static/js/`

The parent console's scripts: plain classic scripts sharing one global scope, loaded **in
this order** by [`../index.html`](../index.html). No build step, no dependencies.

- [`core.js`](core.js) — `api()`, `escapeHtml`, tabs, login, the pairing QRs, the connection
  monitor, `refreshLive()` (the poll that drives every card), 🔐 robot access, and boot.
- [`insights.js`](insights.js) — 📈 insights + the 🔌 connection strip, 🛡️ safety, and
  `armErase` (the two-click erase). `sim/test_console_insights.mjs` mutates this file.
- [`memory.js`](memory.js) — 🧠 what Moxie remembers (read, erase, correct).
- [`settings.js`](settings.js) — ⚙️ settings, the paired-robot card, 🎨 Moxie's look, 📅 today's plan.
- [`perform.js`](perform.js) — 🎭 Be Moxie and 🎬 rehearsal.
- [`voice-brain.js`](voice-brain.js) — 🎚️ voice and 🧠 brain pickers.
- [`content.js`](content.js) — 📦 content packs and the ✍️ editor.
- [`tryit.js`](tryit.js) — 💬 Try it: a preview conversation with the real brain, no robot;
  `trySend` is its one brain call, click-bound. `sim/test_console_tryit.mjs` mutates this file.

---
📖 [static](../README.md) · [Back to top](../../../README.md)
