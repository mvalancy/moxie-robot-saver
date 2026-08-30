# 🤝 Shared Claude agents & skills

This folder ships **Claude-compatible agents and skills** with the repo, so the hard-won Moxie
knowledge is shared, not trapped on one laptop. Clone the repo, open it in
[Claude Code](https://claude.com/claude-code) (or any Claude Agent SDK harness), and you get an
expert helper for reviving and extending Moxie.

> 💚 This is to help kids everywhere get their robot back. Improve these and send a PR.

## Agents (`agents/`)
Specialists you can delegate to.
- **`moxie-revival-guide`** — walks a Moxie *owner* through bringing their robot back to life
  (firmware check → Wi-Fi QR → endpoint QR → talking), grounded in `docs/`.
- **`moxie-protocol-expert`** — a *developer's* deep expert on the reverse-engineered protocol (REST,
  crypto, pairing QR, MQTT/conversation) for extending the project.

## Skills (`skills/`)
Task recipes Claude can invoke by name.

**For owners / developers using Moxie:**
- **`generate-pairing-qr`** — make a Wi-Fi pairing QR from the CLI or server.
- **`factory-reset-moxie`** — unpair or factory-reset a paired robot.
- **`find-moxie-on-lan`** — locate the robot's IP after it joins Wi-Fi.

**For contributors extending the reverse-engineering** (codified from the long deconstruction — the
techniques + gotchas that saved the most time):
- **`reversing-moxie-firmware`** — the RE loop: pick the next unexplored thread, decompile, write clean-room-complete `v24.10.803`-stamped findings. The "named-but-not-enumerated" and clean-room-sufficiency lenses.
- **`decompiling-native-libs`** — the escalation ladder for native `.so` libs (`nm`/`strings` → capstone → **PyGhidra** on a JRE-only host) and Unity assets (UnityPy), with the string-ref + project-lock gotchas that cost real time.
- **`publishing-moxie-docs`** — rebuild the static docs-explorer bundle and run every guard (links/anchors, consistency, mermaid, headless explorer) so the doc tree stays consistent top-to-bottom.

## How they stay accurate
Every agent and skill points at the **source-of-truth docs** in this repo (`docs/reverse-engineering/`,
`docs/architecture/`, `docs/guides/`). When the docs improve, the helpers improve.

---
📖 [Back to top](../README.md)
