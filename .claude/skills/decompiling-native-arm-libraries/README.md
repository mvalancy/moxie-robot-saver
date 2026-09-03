# 🛠️ Decompiling native ARM `.so` libraries

Inspect and decompile native ARM .so libraries — escalating from nm/strings to capstone to Ghidra via PyGhidra — to recover a device's native logic (dispatch tables, GOT-indirected strings, the hardware C API, obfuscation). Use when symbols alone aren't enough and you need what a native function actually does. Works on any ARM Android/Linux .so.

Invoke it by name (`decompiling-native-arm-libraries`) — it is a **shared agent skill**, so any agent working in this repo can load it instead of re-deriving the method.

| File | Purpose |
|---|---|
| [`SKILL.md`](SKILL.md) | The skill itself — instructions the agent follows. |

## What it covers

- Tier 1 — symbols & strings (always first)
- Tier 2 — disassembly (capstone)
- Tier 3 — Ghidra, driven by **PyGhidra** (the reliable headless path)
- Obfuscation (when strings are deliberately hidden)
- Worked example (Moxie)

---
📖 [Skills](../README.md) · [Back to top](../../../README.md)
